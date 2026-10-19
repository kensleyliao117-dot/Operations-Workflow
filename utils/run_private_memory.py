"""私密作品的发布核实、真实指标、经验入库和检索联调；不执行发布。"""
import argparse
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import select, text

from database import Sessionlocal
from memory_database import MemorySession
from memory.models import Feedback, OptimizationMemory, PostMetrics, PublishedPost
from memory.schemas import CollectMetricsRequest, FeedbackRequest, MemoryRead, MetricsRead
from memory.services import publications, metrics, feedback
from memory import retriever
from models.content import Content
from models.publish_record import PublishRecord
from models.task import Task
from schemas.publish_record import PublishResult, PublishRecordRead
from services import publish_record
from services.common import get_record
from services.platform_account import require_account
from services.publishing_snapshot import snapshot
from collectors.rednote_creator import POSTED_PATH
from utils.run_private_flow import REPORT, save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["confirm", "collect", "analyze", "verify"])
    args = parser.parse_args()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    with Sessionlocal() as db, MemorySession() as memory_db:
        db.info.update(user_id=report["user_id"], memory_session_factory=MemorySession)
        if args.stage == "confirm":
            observation = json.loads((REPORT.parent / "publication-observation.json").read_text(encoding="utf-8"))
            notes = [item["note"] for item in observation["observed_notes"] if item["path"] == POSTED_PATH]
            if len(notes) != 1 or notes[0].get("permission_msg") != "仅自己可见":
                raise RuntimeError("A uniquely verified private publication is required")
            note = notes[0]
            account = require_account(db, report["account_id"], "rednote")
            if account.platform_user_id != report["browser_account_id"]:
                raise RuntimeError("Platform account mismatch")
            content = get_record(db, Content, report["content_id"])
            _, _, payload = snapshot(db, content, account, visibility="private")
            editor = observation.get("published_editor") or {}
            if (note.get("display_title") != payload.title or editor.get("title") != payload.title
                    or editor.get("body") != payload.editor_body or editor.get("image_count") != len(payload.images)):
                raise RuntimeError("Published content does not match the approved version")
            if "confirmed_publish_record_id" not in report:
                old = get_record(db, PublishRecord, report["publish_record_id"])
                result = publish_record.retry_publish_record(db, old.id,
                    idempotency_key=f"private-e2e-confirm-existing-{old.id}")
                report["confirmed_publish_record_id"] = result.id
                save(report)
            actual = datetime.strptime(note["time"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone(timedelta(hours=8)))
            result = publish_record.record_publish_result(db, report["confirmed_publish_record_id"],
                PublishResult(publish_status="published", platform_post_id=note["id"],
                    account_id=account.id, published_at=actual))
            report["confirmed_publication"] = PublishRecordRead.model_validate(result).model_dump(mode="json")
            old_id = report["publish_record_id"]
            if old_id != result.id:
                previous = report.setdefault("previous_publish_record_ids", [])
                if old_id not in previous:
                    previous.append(old_id)
            report["publish_record_id"] = result.id
            report["publication"] = report["confirmed_publication"]
            report["publication_confirmation"] = {"method": "manual_ui_verified", "timestamp_precision": "displayed_minute",
                "content_verified": True, "image_count_verified": True, "automatic_publish_verified": False}
            save(report)
        elif args.stage == "collect":
            record_id = report["confirmed_publish_record_id"]
            result = metrics.collect(db, memory_db, record_id,
                CollectMetricsRequest(idempotency_key=f"private-e2e-creator-metrics-{record_id}-001"))
            report["memory_metrics"] = MetricsRead.model_validate(result).model_dump(mode="json")
            save(report)
        elif args.stage == "analyze":
            result = feedback.analyze(db, memory_db, report["confirmed_publish_record_id"],
                FeedbackRequest(metrics_ids=[report["memory_metrics"]["id"]],
                    user_note="本次为仅自己可见的流程测试，产品为虚构示例。单篇私密笔记的零互动不能用于判断公开流量、受众偏好、文案或配图效果。仅提出下一轮可验证实验，不将任何写法视为已验证有效。"))
            previous = report.get("memory_experience", {}).get("id")
            if previous is not None and previous != result.id:
                history = report.setdefault("previous_memory_experience_ids", [])
                if previous not in history:
                    history.append(previous)
            report["memory_experience"] = MemoryRead.model_validate(result).model_dump(mode="json")
            save(report)
        elif args.stage == "verify":
            post = publications.find_publication(db, memory_db, report["confirmed_publish_record_id"])
            result = retriever.retrieve(db, memory_db, report["product_id"], "通勤耳机 标题 开头 正文 下一轮实验",
                platform="rednote", account_id=report["account_id"])
            if report["memory_experience"]["id"] not in {item.id for item in result}:
                raise RuntimeError("New experience was not retrieved")
            generation_refs, status = retriever.for_generation(db, report["product_id"], "通勤耳机 标题 开头 正文 下一轮实验")
            if status != "used" or report["memory_experience"]["id"] not in {item.id for item in generation_refs}:
                raise RuntimeError("Generation service did not retrieve the new experience")
            stored = memory_db.get(OptimizationMemory, report["memory_experience"]["id"])
            evidence = memory_db.get(Feedback, stored.feedback_id)
            source = memory_db.get(PostMetrics, report["memory_metrics"]["id"])
            if stored.post_id != post.id or evidence.post_id != post.id or source.post_id != post.id:
                raise RuntimeError("Evidence relations do not match")
            if source.source != "rednote_creator" or post.content_snapshot.get("visibility") != "private":
                raise RuntimeError("Metric source or private-test context is missing")
            report["memory_verification"] = {"business_database": db.scalar(text("SELECT current_database()")),
                "memory_database": memory_db.scalar(text("SELECT current_database()")), "post_id": post.id,
                "feedback_id": evidence.id, "experience_id": stored.id, "experience_version": stored.version,
                "retrieved_ids": [item.id for item in result], "generation_memory_status": status,
                "generation_memory_ids": [item.id for item in generation_refs],
                "task_status": get_record(db, Task, report["task_id"]).status}
            save(report)
        summary = {key: value for key, value in report.items() if key.startswith("memory_") or key.startswith("confirmed_") or key == "publication_confirmation"}
        if "memory_experience" in summary:
            summary["memory_experience"] = {key: value for key, value in summary["memory_experience"].items() if key != "result"}
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
