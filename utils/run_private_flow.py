"""真实私密演练的分阶段入口；生成、人工审核和发布相互独立。"""
import argparse
import json
from pathlib import Path

from sqlalchemy import select

import config
from database import Sessionlocal
from memory_database import MemorySession
from models.document_chunk import DocumentChunk
from models.rednote import RednoteCase
from models.task import Task
from models.content import Content
from models.platform_account import PlatformAccount
from models.workflow import TaskWorkflow
from models.publish_record import PublishRecord
from schemas.rednote import RednoteAnalyzeRequest, RednoteCaseCreate
from schemas.task import TaskCreate
from schemas.content_generation import TaskContentGenerationRequest
from schemas.image_generation import ImageGenerationRequest
from schemas.review import ReviewCreate
from schemas.platform_account import PlatformAccountCreate
from schemas.publish_record import PublishRequest, PublishRecordRead
from schemas.workflow import WorkflowResumeRequest
from services import rednote, task as task_service, workflow, image_generation
from services import review, platform_account, publish_record, publishing
from services.common import get_record, owned_query
from rag import retriever


REPORT = config.STORAGE_DIR / "private-e2e" / "report.json"
REQUIREMENT = """为聆屿 AirS1 耳机写一篇小红书图文笔记，用通勤场景自然开篇，语气平实。
不使用第一人称体验，不编造产品功效、参数、使用效果或优惠。
标题不超过20个汉字，正文200到350字，3到5个话题。
产品事实只能引用检索资料，爆款案例仅供结构和开头创意参考。本次为私密工作流测试。"""
REQUIREMENT += "\n资料为虚构示例产品，仅用于流程演示。正文明确注明‘虚构产品，流程测试’，不得写成真实销售或实际测评。"


def save(report):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "generate", "images", "approve", "publish", "retry-publish", "show"])
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--product-id", type=int, required=True)
    parser.add_argument("--document-id", type=int, default=1)
    parser.add_argument("--public-case-id", type=int, default=1)
    parser.add_argument("--approved-content-id", type=int)
    args = parser.parse_args()
    report = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {
        "user_id": args.user_id, "product_id": args.product_id, "visibility": "private"}
    if report["user_id"] != args.user_id or report["product_id"] != args.product_id:
        raise RuntimeError("Existing report belongs to another user/product")
    with Sessionlocal() as db:
        db.info.update(user_id=args.user_id, memory_session_factory=MemorySession)
        if args.stage == "prepare":
            if not db.scalar(owned_query(db, DocumentChunk).where(DocumentChunk.product_id == args.product_id).limit(1)):
                print("Indexing product document", flush=True)
                result = retriever.index_document(db, args.product_id, args.document_id)
                report["indexed"] = {"product_id": result.product_id, "document_id": result.document_id}
                save(report)
            if "case_id" not in report:
                # 经明确选择的公开原文快照重新导入，不引用其他用户的私有分析。
                original = db.get(RednoteCase, args.public_case_id)
                if original is None:
                    raise RuntimeError("Public source snapshot not found")
                values = {"name": original.name, "link": original.link, "body": original.body,
                          "image_urls": original.image_urls or [], "metrics": original.metrics or {},
                          "metrics_raw": original.metrics_raw or {}, "author_platform_id": original.author_platform_id}
                existing = db.scalar(owned_query(db, RednoteCase).where(RednoteCase.link == values["link"]))
                case = existing if existing is not None else rednote.create_case(db, RednoteCaseCreate(**values))
                report.update(case_id=case.id, case_source="public_snapshot_import")
                save(report)
            if "analysis_id" not in report:
                print("Analyzing public case", flush=True)
                result = rednote.analyze_case(db, report["case_id"], RednoteAnalyzeRequest())
                report.update(analysis_id=result.id, analysis_version=result.version, analysis_model=result.model)
                save(report)
        elif args.stage == "generate":
            if "analysis_id" not in report:
                raise RuntimeError("Complete prepare first")
            if "task_id" not in report:
                created = task_service.create_task(db, TaskCreate(product_id=args.product_id, platform_name="rednote",
                    task_type="generate", input_text=REQUIREMENT), user_id=args.user_id)
                report["task_id"] = created.id
                save(report)
            print("Generating text through LangGraph", flush=True)
            current = get_record(db, Task, report["task_id"])
            if db.get(TaskWorkflow, report["task_id"]) is not None:
                result = workflow.retry_workflow(db, report["task_id"])
            else:
                result = workflow.start_workflow(db, report["task_id"],
                    TaskContentGenerationRequest(analysis_ids=[report["analysis_id"]]))
            report["workflow"] = result.model_dump(mode="json")
            report["content_id"] = result.content_id
            save(report)
        elif args.stage == "images":
            if "image_result" not in report:
                print("Generating one illustration", flush=True)
                result = image_generation.generate_and_save_images(db, report["content_id"],
                    ImageGenerationRequest(count=1, style_requirement="简洁的通勤氛围配图，柔和自然光与留白，不绘制未获资料支持的产品外观，不含参数、功效、价格文字"))
                report.update(image_result=result.model_dump(mode="json"), content_id=result.content.id)
                save(report)
        elif args.stage == "approve":
            if args.approved_content_id != report["content_id"] or "browser_account_id" not in report:
                raise RuntimeError("Explicit approval for this exact content and browser account is required")
            if "review_id" not in report:
                result = review.create_review(db, ReviewCreate(task_id=report["task_id"],
                    content_id=report["content_id"], status="approved", comment="文案和配图经人工确认，仅自己可见发布。"))
                report["review_id"] = result.id
                save(report)
            result = workflow.resume_workflow(db, report["task_id"], WorkflowResumeRequest(action="review"))
            report["workflow"] = result.model_dump(mode="json")
            account = db.scalar(owned_query(db, PlatformAccount).where(
                PlatformAccount.platform == "rednote", PlatformAccount.platform_user_id == report["browser_account_id"]))
            if account is None:
                account = platform_account.create_account(db, PlatformAccountCreate(platform="rednote",
                    platform_user_id=report["browser_account_id"], display_name="叁酒"))
            report["account_id"] = account.id
            save(report)
        elif args.stage in {"publish", "retry-publish"}:
            if "review_id" not in report or "account_id" not in report:
                raise RuntimeError("Human approval and account confirmation are required")
            if "publish_record_id" not in report:
                result = publish_record.create_publish_record(db, PublishRequest(task_id=report["task_id"],
                    content_id=report["content_id"], platform="rednote", account_id=report["account_id"],
                    publisher_backend="chrome", visibility="private",
                    idempotency_key=f"private-e2e-task-{report['task_id']}-content-{report['content_id']}"))
                report["publish_record_id"] = result.id
                save(report)
            elif args.stage == "retry-publish":
                old = get_record(db, PublishRecord, report["publish_record_id"])
                if old.publish_status != "failed" or old.submitted_at is not None:
                    raise RuntimeError("Only a confirmed pre-submission failure can be retried by this test utility")
                result = publish_record.retry_publish_record(db, old.id,
                    idempotency_key=f"private-e2e-retry-after-{old.id}-content-{report['content_id']}")
                report.setdefault("previous_publish_record_ids", []).append(old.id)
                report["publish_record_id"] = result.id
                save(report)
            result = publishing.execute_publish(db, report["publish_record_id"])
            report["publication"] = PublishRecordRead.model_validate(result).model_dump(mode="json")
            save(report)
        if args.stage == "show":
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        else:
            summary = {k: v for k, v in report.items() if k not in {"workflow", "image_result"}}
            if "workflow" in report:
                summary["workflow_status"] = report["workflow"]["status"]
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
