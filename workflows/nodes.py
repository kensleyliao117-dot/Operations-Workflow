"""节点只编排已有业务 Service，不重复实现 RAG、模型调用或内容保存。"""
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from sqlalchemy import select

from models.content import Content
from models.review import Review
from models.task import Task
from schemas.content_generation import TaskContentGenerationRequest
from schemas.workflow import WorkflowGenerationRequest
from schemas.image_generation import ImageGenerationRequest
from schemas.task import TaskStatusUpdate
from services import content_generation, rules as rules_service, review as review_service, task as task_service
from schemas.rules import RuleCheckRead
from schemas.notices import user_notices
from services.common import current_user_id, get_record, write_transaction
from services.exceptions import ConflictError, ServiceError
from .state import ContentWorkflowState, WorkflowContext


def load_analyses(state: ContentWorkflowState, runtime: Runtime[WorkflowContext]):
    db = runtime.context.db
    task = get_record(db, Task, state["task_id"])
    options = WorkflowGenerationRequest(**state["options"])
    validated = content_generation.validate_task_generation(db, task, options.text_options())
    references = content_generation._selected_analyses(db, options.analysis_ids, validated.platform)
    values = [ref.model_dump(mode="json") for ref in references]
    db.rollback()
    return {"analysis_references": values, "stage": "generating"}


def generate_content(state: ContentWorkflowState, runtime: Runtime[WorkflowContext]):
    db = runtime.context.db
    if state["user_id"] != current_user_id(db):
        raise ConflictError("工作流身份与当前请求不一致")
    options = WorkflowGenerationRequest(**state["options"])
    operation_key = f"{state['run_id']}:{state['cycle']}"
    if options.output_mode == "images":
        from services.image_generation import prepare_image_brief
        content = prepare_image_brief(db, state["task_id"], options.text_options(), operation_key)
        content_id, version = content.id, content.version
    else:
        result = content_generation.generate_and_save_content(db, state["task_id"],
            options.text_options(), operation_key=operation_key, defer_review=True)
        content_id, version = result.content_id, result.version
        with write_transaction(db):
            content = get_record(db, Content, content_id)
            content.generation_metadata = {**(content.generation_metadata or {}), "output_mode": options.output_mode}
    return {"content_id": content_id, "content_version": version, "stage": "platform_rules", "output_mode": options.output_mode}


def generation_route(state):
    return "generate_images" if state.get("output_mode") in {"images", "text_images"} else "platform_rules"


def generate_images(state: ContentWorkflowState, runtime: Runtime[WorkflowContext]):
    from services import image_generation
    options = WorkflowGenerationRequest(**state["options"])
    result = image_generation.generate_and_save_images(runtime.context.db, state["content_id"],
        ImageGenerationRequest(style_requirement=options.image_style_requirement,
            count=options.image_count, size=options.image_size, model_id=options.model_id,
            image_model_id=options.image_model_id),
        defer_review=True, operation_key=f"{state['run_id']}:{state['cycle']}:images")
    return {"content_id": result.content.id, "content_version": result.content.version, "stage": "platform_rules"}


def platform_rules(state: ContentWorkflowState, runtime: Runtime[WorkflowContext]):
    db = runtime.context.db
    warning, value = None, None
    try:
        check = rules_service.check_content(db, state["content_id"], force=state.get("decision") == "recheck")
        check = rules_service.apply_check_to_task(db, check.id)
        value = RuleCheckRead.model_validate(check).model_dump(mode="json")
    except ServiceError as exc:
        db.rollback()
        with write_transaction(db):
            task = get_record(db, Task, state["task_id"], lock=True)
            content = latest_content(db, task.id)
            if task.status not in {"running", "pending_review"} or content is None or content.id != state["content_id"]:
                raise ConflictError("任务或内容已变化，请刷新状态") from None
            rules_service.require_review_ready(db, task, content)
            task.status = "pending_review"
        warning = "规则检查未完成，仅作提示，请人工核对：" + str(exc)
    return {"rule_check": value, "rule_check_warning": warning, "stage": "waiting_review"}


def latest_content(db, task_id):
    get_record(db, Task, task_id)
    return db.scalar(select(Content).where(Content.task_id == task_id).order_by(Content.version.desc()).limit(1))


def validate_resume(db, state, action):
    """恢复前检查真实业务状态，避免无效请求消费审核中断。"""
    task = get_record(db, Task, state["task_id"])
    from services.image_generation import generation_in_progress
    if generation_in_progress(task):
        raise ConflictError("配图正在生成，请完成后再恢复工作流")
    if task.status in {"cancelled", "publishing", "completed", "failed"}:
        raise ConflictError("当前任务状态不能恢复人工审核节点")
    content = latest_content(db, task.id)
    if content is None:
        raise ConflictError("任务没有可审核内容")
    if action == "review":
        review = db.scalar(select(Review).where(Review.task_id == task.id, Review.content_id == content.id)
                           .order_by(Review.id.desc()).limit(1))
        if (content.id != state["content_id"] or review is None or review.id == state.get("review_id")
                or task.status not in {"approved", "rejected"} or task.status != review.status):
            raise ConflictError("请先通过审核接口审核工作流当前的最新内容版本")
        return {"action": action, "review_id": review.id}
    if action == "submit_changes":
        if content.version <= state["content_version"] or task.status not in {"running", "rejected", "pending_review"}:
            raise ConflictError("请先保存新的人工修改版本，再提交审核")
        return {"action": action, "content_id": content.id}
    if action == "submit_review":
        if task.status not in {"running", "pending_review"} or content.id != state["content_id"]:
            raise ConflictError("请先提交最新版本；已驳回的内容需要修改后再审核")
        return {"action": action}
    if action == "recheck":
        if content.id != state["content_id"] or task.status not in {"running", "pending_review"}:
            raise ConflictError("请先提交最新修改版本，审核驳回后需修改或重新生成")
        return {"action": action}
    if task.status not in {"running", "rejected", "pending_review"}:
        raise ConflictError("当前任务状态不允许重新生成")
    return {"action": action}


def human_review(state: ContentWorkflowState, runtime: Runtime[WorkflowContext]):
    # interrupt 之前不执行写操作，恢复时该节点会从头运行。
    command = interrupt({"task_id": state["task_id"], "content_id": state["content_id"],
        "content_version": state["content_version"], "stage": state["stage"],
        "rule_check": state.get("rule_check"), "notices": user_notices(),
        "actions": ["review", "submit_changes", "submit_review", "regenerate", "recheck"]})
    db = runtime.context.db
    expected = validate_resume(db, state, command["action"])
    if any(command.get(key) != value for key, value in expected.items()):
        raise ConflictError("审核或内容版本已变化，请获取最新状态")
    if command["action"] == "review":
        review = get_record(db, Review, command["review_id"])
        review_service.require_latest_content(db, state["task_id"], review.content_id)
        result = {"decision": review.status, "review_id": review.id,
                  "stage": "approved" if review.status == "approved" else "waiting_changes"}
        db.rollback()
        return result
    if command["action"] == "submit_changes":
        task = get_record(db, Task, state["task_id"])
        if task.status == "rejected":
            task_service.update_task_status(db, task.id, TaskStatusUpdate(status="running"))
        content = get_record(db, Content, command["content_id"])
        result = {"content_id": content.id, "content_version": content.version,
                  "decision": "modified", "stage": "waiting_review", "review_id": None}
        db.rollback()
        return result
    if command["action"] == "recheck":
        db.rollback()
        return {"decision": "recheck", "stage": "checking_rules"}
    if command["action"] == "submit_review":
        with write_transaction(db):
            task = get_record(db, Task, state["task_id"], lock=True)
            rules_service.require_review_ready(db, task, latest_content(db, task.id))
            task.status = "pending_review"
        return {"decision": "submit_review", "stage": "waiting_review"}
    options = WorkflowGenerationRequest(**command["options"])
    task = get_record(db, Task, state["task_id"])
    content_generation.validate_task_generation(db, task, options.text_options())
    db.rollback()
    return {"options": options.model_dump(mode="json"), "cycle": state["cycle"] + 1,
            "decision": "regenerate", "stage": "loading_analyses", "review_id": None}


def review_route(state: ContentWorkflowState):
    return {"approved": "ready_to_publish", "rejected": "human_review",
            "modified": "platform_rules", "recheck": "platform_rules", "submit_review": "human_review",
            "regenerate": "load_analyses"}[state["decision"]]


def ready_to_publish(state: ContentWorkflowState):
    """审核通过后结束本阶段，实际发布仍由已有发布接口处理。"""
    return {"stage": "approved"}
