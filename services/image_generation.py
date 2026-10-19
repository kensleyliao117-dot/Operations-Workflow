"""组合产品检索、图片 Prompt、千问生图、文件保存及审核提交。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

import config
from agents import image_prompt_agent
from integrations.llm.image import QwenImageClient
from models.content import Content
from models.product import Product
from models.task import Task
from models.workflow import TaskWorkflow
from rag import retriever
from schemas.content import ContentCreate, ContentRead
from schemas.image_generation import GeneratedImageRead, ImageGenerationRequest, ImageGenerationSavedResult
from schemas.rag import RetrievedSourceRead
from schemas.rules import RuleCheckRead
from schemas.workflow import WorkflowResumeRequest
from . import content as content_service, rules as rules_service
from .analysis_models import select_model
from .common import current_user_id, get_record, write_transaction
from .exceptions import ConflictError, InvalidInputError, NotFoundError, ServiceError, ServiceUnavailableError


logger = logging.getLogger(__name__)
ATTEMPT_LEASE = timedelta(minutes=30)
MAX_ASSETS = 20


def _attempt(task):
    value = (task.checkpoint or {}).get("image_generation")
    return value if isinstance(value, dict) else {}


def generation_in_progress(task):
    value = _attempt(task)
    if value.get("status") != "running":
        return False
    try:
        return datetime.fromisoformat(value["expires_at"]) > datetime.now(timezone.utc)
    except (ValueError, TypeError, KeyError):
        return True


def _start(db, content_id, options):
    with write_transaction(db):
        content = content_service.get_content(db, content_id)
        task = get_record(db, Task, content.task_id, lock=True)
        if task.status not in {"running", "pending_review", "rejected"}:
            raise ConflictError("仅运行中、待审核或已驳回的任务可以生成配图")
        if (task.platform_name or "rednote").strip().lower() != "rednote":
            raise InvalidInputError("当前仅支持 rednote 配图，其他平台需单独配置")
        if not task.product_id:
            raise InvalidInputError("任务必须关联产品")
        product = get_record(db, Product, task.product_id)
        if content.version != content_service._next_version(db, task.id) - 1:
            raise ConflictError("只能为最新文案版本生成配图")
        if generation_in_progress(task) or (task.checkpoint or {}).get("content_generation", {}).get("status") == "running":
            raise ConflictError("该任务已有生成请求正在执行，请勿重复提交")
        if len(content.image_assets or []) + options.count > MAX_ASSETS and not options.replace_existing:
            raise InvalidInputError("一个内容版本最多关联 20 张图片，请选择替换图片")
        snapshot = {"task_id": task.id, "product_id": product.id, "product_name": product.name,
                    "platform": task.platform_name, "user_id": current_user_id(db),
                    "requirement": task.input_text, "previous_status": task.status,
                    "content": ContentRead.model_validate(content).model_dump(mode="json")}
        attempt_id = uuid4().hex
        now = datetime.now(timezone.utc)
        task.status = "running"
        task.checkpoint = {**(task.checkpoint or {}), "image_generation": {
            "attempt_id": attempt_id, "status": "running", "content_id": content_id,
            "started_at": now.isoformat(), "expires_at": (now + ATTEMPT_LEASE).isoformat(),
        }}
    return snapshot, attempt_id


def _failure(db, snapshot, attempt_id):
    try:
        with write_transaction(db):
            task = get_record(db, Task, snapshot["task_id"], lock=True)
            if _attempt(task).get("attempt_id") != attempt_id or _attempt(task).get("status") != "running":
                return
            # 已取消或出现新版本时，仅记录本次失败，不恢复旧任务状态。
            if task.status == "running" and content_service._next_version(db, task.id) == snapshot["content"]["version"] + 1:
                task.status = snapshot["previous_status"]
            task.checkpoint = {**(task.checkpoint or {}), "image_generation": {
                **_attempt(task), "status": "failed", "error_message": "配图生成或保存失败，原文案和图片未改变",
            }}
    except NotFoundError:
        pass
    except SQLAlchemyError as exc:
        logger.error("Cannot record image generation failure: %s", type(exc).__name__)


def _directory(user_id, product_id):
    root = config.GENERATED_IMAGE_DIR.resolve()
    directory = root / f"user_{user_id}" / f"product_{product_id}"
    if directory.resolve() != directory:
        raise InvalidInputError("图片存储路径无效")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def image_path(db, content_id: int, index: int) -> Path:
    content = content_service.get_content(db, content_id)
    task = get_record(db, Task, content.task_id)
    if index < 0 or index >= len(content.image_assets or []):
        raise NotFoundError("图片不存在")
    key = content.image_assets[index].get("storage_key", "")
    prefix = f"generated_images/user_{current_user_id(db)}/product_{task.product_id}/"
    if not isinstance(key, str) or not key.startswith(prefix):
        raise NotFoundError("图片不存在")
    filename = key.removeprefix(prefix)
    # 文件名仅接受生成模块写入的 UUID，不读取任意路径或远程 URL。
    if len(filename) != 36 or not filename.endswith(".png") or any(c not in "0123456789abcdef" for c in filename[:-4]):
        raise NotFoundError("图片不存在")
    root = config.GENERATED_IMAGE_DIR.resolve()
    expected = root / f"user_{current_user_id(db)}" / f"product_{task.product_id}"
    path = expected / filename
    if expected.resolve() != expected or path.resolve().parent != expected or not path.is_file():
        raise NotFoundError("图片不存在")
    return path


def _save(db, snapshot, attempt_id, options, draft, result, assets, sources):
    with write_transaction(db):
        task = get_record(db, Task, snapshot["task_id"], lock=True)
        if task.status != "running" or _attempt(task).get("attempt_id") != attempt_id or _attempt(task).get("status") != "running":
            raise ConflictError("任务已中断或状态已变化，本次图片未保存")
        get_record(db, Product, snapshot["product_id"])
        if (task.product_id != snapshot["product_id"] or task.platform_name != snapshot["platform"]
                or task.input_text != snapshot["requirement"]
                or content_service._next_version(db, task.id) != snapshot["content"]["version"] + 1):
            raise ConflictError("生成期间任务或文案版本已变化，请获取最新版本后重试")
        original = snapshot["content"]
        values = {name: original[name] for name in ("task_id", "title", "body", "tags", "video_script")}
        previous_assets = [] if options.replace_existing else original["image_assets"]
        content = content_service.add_content_version(db, ContentCreate(
            **values, image_prompt=draft.prompt, image_assets=[*previous_assets, *assets]))
        content.generation_key = snapshot.get("operation_key")
        metadata = deepcopy(original["generation_metadata"] or {})
        metadata.setdefault("sources", sources)
        metadata["image_generation"] = {"attempt_id": attempt_id, "source_content_id": original["id"],
            "product_id": snapshot["product_id"], "model": result.model, "prompt_model": snapshot["prompt_model"],
            "prompt_model_profile": snapshot["prompt_model_profile"],
            "image_model_profile": snapshot.get('image_model_profile'),
            "request_id": result.request_id, "prompt": draft.prompt, "negative_prompt": draft.negative_prompt,
            "size": options.size, "count": options.count,
            "watermark": True if snapshot.get('image_protocol', 'qwen_image') == 'qwen_image' else None,
            "prompt_extend": False if snapshot.get('image_protocol', 'qwen_image') == 'qwen_image' else None,
            "sources": sources, "storage_keys": [asset["storage_key"] for asset in assets]}
        content.generation_metadata = metadata
        db.flush()
        task.checkpoint = {**(task.checkpoint or {}), "step": "platform_rules", "image_generation": {
            **_attempt(task), "status": "succeeded", "content_id": content.id, "version": content.version,
            "model": result.model, "request_id": result.request_id,
        }}
        saved = ContentRead.model_validate(content)
    return saved


def submit_for_review(db, content_id: int):
    """配图保存后的审核提交可以单独重试，不重新调用生图 API。"""
    content = content_service.get_content(db, content_id)
    task = get_record(db, Task, content.task_id)
    if not (content.generation_metadata or {}).get("image_generation"):
        raise InvalidInputError("该内容不是已保存的配图版本")
    if generation_in_progress(task):
        raise ConflictError("配图正在生成，请完成后再提交审核")
    if task.status not in {"running", "pending_review"} or content.version != content_service._next_version(db, task.id) - 1:
        raise ConflictError("只能为运行中或待审核任务的最新配图版本提交审核")
    if db.get(TaskWorkflow, task.id) is not None:
        from . import workflow as workflow_service
        state = workflow_service.get_workflow(db, task.id)
        action = "recheck" if state.content_id == content_id else "submit_changes"
        workflow = workflow_service.resume_workflow(db, task.id, WorkflowResumeRequest(action=action))
        check = RuleCheckRead.model_validate(workflow.rule_check) if workflow.rule_check else None
        return workflow.status, check, workflow
    try:
        check = rules_service.check_content(db, content_id)
        check = rules_service.apply_check_to_task(db, check.id)
        value = RuleCheckRead.model_validate(check)
    except ServiceError:
        db.rollback()
        with write_transaction(db):
            task = get_record(db, Task, content.task_id, lock=True)
            latest = content_service.get_content(db, content_id)
            if latest.version != content_service._next_version(db, task.id) - 1 or task.status not in {"running", "pending_review"}:
                raise ConflictError("任务或内容已变化，请刷新状态")
            rules_service.require_review_ready(db, task, latest)
            task.status = "pending_review"
        value = None
    return ("waiting_review", value, None)


def _response(db, saved, *, review_status, review_error=None, rule_check=None, workflow=None):
    record = saved.generation_metadata["image_generation"]
    task = get_record(db, Task, saved.task_id)
    return ImageGenerationSavedResult(content=saved, image_model=record["model"], prompt_model=record["prompt_model"],
        request_id=record["request_id"], images=[GeneratedImageRead(index=i, storage_key=asset.storage_key,
            download_path=f"/contents/{saved.id}/images/{i}") for i, asset in enumerate(saved.image_assets)],
        task_status=task.status, review_status=review_status, review_error=review_error,
        rule_check=rule_check, workflow=workflow)


def retry_review_submission(db, content_id):
    saved = ContentRead.model_validate(content_service.get_content(db, content_id))
    status, check, workflow = submit_for_review(db, content_id)
    return _response(db, saved, review_status=status, rule_check=check, workflow=workflow)


def generate_and_save_images(db, content_id: int, options: ImageGenerationRequest, *, defer_review=False,
                             operation_key=None) -> ImageGenerationSavedResult:
    original = content_service.get_content(db, content_id)
    if operation_key:
        saved = db.scalar(select(Content).where(Content.task_id == original.task_id, Content.generation_key == operation_key))
        if saved is not None:
            return _response(db, ContentRead.model_validate(saved), review_status="saved")
    snapshot, attempt_id = _start(db, content_id, options)
    snapshot["operation_key"] = operation_key
    written = []
    try:
        profile = select_model(db, options.model_id)
        snapshot["prompt_model"] = profile.model
        snapshot["prompt_model_profile"] = profile.snapshot()
        from .resource_models import select_profile
        from integrations.llm.resource_clients import ResourceImageClient
        image_profile = select_profile(db, 'image', options.image_model_id)
        if image_profile and options.size not in image_profile.image_sizes:
            raise InvalidInputError('图片尺寸不受所选模型支持，请调整尺寸后重新生成')
        snapshot['image_model_profile'] = image_profile.snapshot() if image_profile else None
        snapshot['image_protocol'] = image_profile.protocol if image_profile else 'qwen_image'
        with (ResourceImageClient(image_profile) if image_profile else QwenImageClient()) as client:
            original = snapshot["content"]
            knowledge = retriever.retrieve(db, snapshot["product_id"],
                ((original["title"] + "\n" + original["body"]).strip() or snapshot["requirement"])[:2000], top_k=5, max_context_chars=12000)
            if not knowledge.context.strip() or not knowledge.sources:
                raise InvalidInputError("未检索到产品资料，请先上传并入库资料后再生成配图")
            sources = [RetrievedSourceRead.model_validate(source).model_dump(mode="json") for source in knowledge.sources]
            db.rollback()
            draft = image_prompt_agent.generate_prompt({"platform": "rednote",
                "product": {"id": snapshot["product_id"], "name": snapshot["product_name"]},
                "product_knowledge": {"context": knowledge.context, "sources": sources},
                "content": {name: original[name] for name in ("title", "body", "tags")},
                "user_requirement": snapshot["requirement"],
                "custom_prompt": (original["generation_metadata"] or {}).get("custom_prompt"),
                "case_references": (original["generation_metadata"] or {}).get("analysis_references", []),
                "style_requirement": options.style_requirement, "size": options.size}, profile=profile)
            result = client.generate(draft.prompt, draft.negative_prompt, count=options.count, size=options.size)
            directory = _directory(snapshot["user_id"], snapshot["product_id"])
            assets = []
            for url in result.urls:
                data = client.download(url, size=options.size)
                filename = uuid4().hex + ".png"
                path = directory / filename
                written.append(path)
                with path.open("xb") as output:
                    output.write(data)
                assets.append({"storage_key": f"generated_images/user_{snapshot['user_id']}/product_{snapshot['product_id']}/{filename}",
                               "prompt": draft.prompt})
        saved = _save(db, snapshot, attempt_id, options, draft, result, assets, sources)
    except Exception as exc:
        db.rollback()
        saved = None
        cleanup_safe = True
        try:
            # 提交应答丢失时先核对已落库的尝试，避免删除已关联图片或重复付费生成。
            recovered = db.scalar(select(Content).where(Content.task_id == snapshot["task_id"],
                Content.version == snapshot["content"]["version"] + 1))
            if recovered is not None and (recovered.generation_metadata or {}).get("image_generation", {}).get("attempt_id") == attempt_id:
                saved = ContentRead.model_validate(recovered)
        except SQLAlchemyError:
            db.rollback()
            cleanup_safe = False
            logger.error("Cannot verify image commit; local files retained for reconciliation")
        for path in written if saved is None and cleanup_safe else []:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.error("Cannot remove uncommitted generated image")
        if saved is None:
            _failure(db, snapshot, attempt_id)
            if isinstance(exc, config.ConfigurationError):
                raise ServiceUnavailableError(str(exc)) from None
            if isinstance(exc, (ServiceError, SQLAlchemyError)):
                raise
            raise ServiceUnavailableError("配图生成或保存失败，请稍后重试；原文案未改变") from None
    if defer_review:
        return _response(db, saved, review_status="saved")
    # 文件和新版本已提交；审核提交失败单独返回，避免重复生成产生额外用量。
    try:
        status, check, workflow = submit_for_review(db, saved.id)
        return _response(db, saved, review_status=status, rule_check=check, workflow=workflow)
    except Exception as exc:
        db.rollback()
        error = str(exc) if isinstance(exc, ServiceError) else "审核提交暂时失败，请重试审核提交接口，图片已保存"
        return _response(db, saved, review_status="submission_failed", review_error=error)


def prepare_image_brief(db, task_id, options, operation_key):
    """仅创建图片需求版本，不调用文案生成模型；实际配图复用现有生图服务。"""
    from .content_generation import validate_task_generation, _selected_prompt, _selected_analyses
    with write_transaction(db):
        task = get_record(db, Task, task_id, lock=True)
        existing = db.scalar(select(Content).where(Content.task_id == task_id, Content.generation_key == operation_key))
        if existing is not None:
            return ContentRead.model_validate(existing)
        validated = validate_task_generation(db, task, options)
        custom = _selected_prompt(db, options.prompt_id, validated.platform)
        task.status = "running"
        brief = content_service.add_content_version(db, ContentCreate(task_id=task_id, image_prompt=task.input_text))
        brief.generation_key = operation_key
        brief.generation_metadata = {"output_mode": "images", "product_id": task.product_id,
            "custom_prompt": custom, "prompt_id": options.prompt_id,
            "analysis_references": [value.model_dump(mode="json") for value in _selected_analyses(db, options.analysis_ids, validated.platform)]}
        db.flush()
        result = ContentRead.model_validate(brief)
    return result
