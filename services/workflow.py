"""启动和恢复工作流，身份校验、业务操作与图持久化分别处理。"""
import logging
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from models.content import Content
from models.task import Task
from models.workflow import TaskWorkflow
from schemas.content_generation import TaskContentGenerationRequest
from schemas.workflow import WorkflowRead, WorkflowResumeRequest, WorkflowGenerationRequest
from schemas.notices import user_notices
from workflows import persistence
from workflows.graph import build_graph
from workflows.nodes import validate_resume
from workflows.state import WorkflowContext
from . import content_generation
from .common import current_user_id, get_record, write_transaction
from .exceptions import ConflictError, NotFoundError, ServiceError, ServiceUnavailableError

logger = logging.getLogger(__name__)


def _run(db, task_id):
    get_record(db, Task, task_id)
    run = db.get(TaskWorkflow, task_id)
    if run is None or run.user_id != current_user_id(db):
        raise NotFoundError("该任务尚未启动工作流")
    return run


def _config(run):
    # thread_id 完全由服务器构造，客户端不能读取或恢复其他用户的检查点。
    return {"configurable": {"thread_id": f"user:{run.user_id}:task:{run.task_id}:run:{run.run_id}"}}


def _initial(run):
    return {"task_id": run.task_id, "user_id": run.user_id, "run_id": run.run_id,
            "options": dict(run.options), "cycle": 0, "stage": "loading_analyses", "review_id": None}


def _interrupt(snapshot):
    values = [value for task in snapshot.tasks for value in task.interrupts]
    return values[0].value if values else None


def _status(snapshot):
    if _interrupt(snapshot) is not None:
        if snapshot.values.get("stage") == "waiting_rule_changes":
            return "waiting_rule_changes"
        return "waiting_changes" if snapshot.values.get("stage") == "waiting_changes" else "waiting_review"
    return "approved" if snapshot.values.get("stage") == "approved" and not snapshot.next else "running"


def _response(db, task_id, snapshot):
    run = _run(db, task_id)
    task = get_record(db, Task, task_id)
    values = snapshot.values
    # 任务取消状态优先于历史检查点，查询结果不再暴露可执行的审核操作。
    cancelled = task.status == "cancelled"
    from .image_generation import generation_in_progress
    generating_images = not cancelled and generation_in_progress(task)
    return WorkflowRead(task_id=task_id, run_id=run.run_id, task_status=task.status,
        status="cancelled" if cancelled else "generating_images" if generating_images else ("failed" if run.status == "failed" else _status(snapshot)),
        stage="cancelled" if cancelled else "image_generation" if generating_images else values.get("stage"),
        content_id=values.get("content_id"), content_version=values.get("content_version"),
        rule_check=values.get("rule_check"), next_nodes=[] if cancelled or generating_images else list(snapshot.next),
        interrupt=None if cancelled or generating_images else _interrupt(snapshot),
        error_message=run.error_message, notices=user_notices() + ([values["rule_check_warning"]] if values.get("rule_check_warning") else []))


def _mark_running(db, task_id):
    with write_transaction(db):
        get_record(db, Task, task_id, lock=True)
        run = _run(db, task_id)
        run.status, run.error_message = "running", None


def _drive(db, task_id, graph, graph_config, value):
    try:
        graph.invoke(value, config=graph_config, context=WorkflowContext(db), durability="sync")
        snapshot = graph.get_state(graph_config)
        with write_transaction(db):
            get_record(db, Task, task_id, lock=True)
            run = _run(db, task_id)
            run.status, run.error_message = _status(snapshot), None
            if "options" in snapshot.values:
                run.options = snapshot.values["options"]
        return _response(db, task_id, snapshot)
    except Exception as exc:
        db.rollback()
        try:
            with write_transaction(db):
                get_record(db, Task, task_id, lock=True)
                run = _run(db, task_id)
                run.status = "failed"
                run.error_message = str(exc) if isinstance(exc, ServiceError) else "工作流执行或进度保存失败，请重试"
        except SQLAlchemyError as save_error:
            logger.error("Cannot record workflow failure: %s", type(save_error).__name__)
        if isinstance(exc, (ServiceError, SQLAlchemyError)):
            raise
        logger.error("Workflow failed: %s", type(exc).__name__)
        raise ServiceUnavailableError("工作流执行或进度保存失败，请重试") from None


def start_workflow(db, task_id: int, data: TaskContentGenerationRequest):
    data = WorkflowGenerationRequest(**data.model_dump())
    get_record(db, Task, task_id)
    db.rollback()
    with persistence.open_checkpointer(task_id, exclusive=True) as saver:
        with write_transaction(db):
            task = get_record(db, Task, task_id, lock=True)
            content_generation.validate_task_generation(db, task, data.text_options())
            if db.get(TaskWorkflow, task_id) is not None:
                raise ConflictError("该任务已有工作流，请查询状态或通过恢复接口继续")
            if content_generation._attempt(task).get("status") == "running":
                raise ConflictError("该任务已有文案生成请求正在执行")
            run = TaskWorkflow(task_id=task_id, user_id=current_user_id(db), run_id=uuid4().hex,
                               options=data.model_dump(mode="json"), status="running")
            db.add(run)
            db.flush()
            initial, graph_config = _initial(run), _config(run)
        db.rollback()
        return _drive(db, task_id, build_graph(saver), graph_config, initial)


def get_workflow(db, task_id: int):
    run = _run(db, task_id)
    graph_config = _config(run)
    db.rollback()
    with persistence.open_checkpointer(task_id) as saver:
        return _response(db, task_id, build_graph(saver).get_state(graph_config))


def resume_workflow(db, task_id: int, data: WorkflowResumeRequest):
    graph_config = _config(_run(db, task_id))
    db.rollback()
    with persistence.open_checkpointer(task_id, exclusive=True) as saver:
        graph = build_graph(saver)
        snapshot = graph.get_state(graph_config)
        if _interrupt(snapshot) is None:
            raise ConflictError("工作流未在等待人工操作；执行失败时请调用重试接口")
        command = validate_resume(db, snapshot.values, data.action)
        if data.action == "regenerate":
            options = data.generation or WorkflowGenerationRequest(**snapshot.values["options"])
            content_generation.validate_task_generation(db, get_record(db, Task, task_id), options.text_options())
            command["options"] = options.model_dump(mode="json")
        _mark_running(db, task_id)
        return _drive(db, task_id, graph, graph_config, Command(resume=command))


def retry_workflow(db, task_id: int):
    run = _run(db, task_id)
    graph_config, initial = _config(run), _initial(run)
    db.rollback()
    with persistence.open_checkpointer(task_id, exclusive=True) as saver:
        graph = build_graph(saver)
        snapshot = graph.get_state(graph_config)
        if _interrupt(snapshot) is not None or (snapshot.values and not snapshot.next):
            with write_transaction(db):
                run = _run(db, task_id)
                run.status, run.error_message = _status(snapshot), None
            return _response(db, task_id, snapshot)
        with write_transaction(db):
            task = get_record(db, Task, task_id, lock=True)
            if task.status in {"cancelled", "publishing", "completed"}:
                raise ConflictError("当前任务状态不允许重试工作流")
            # 获得跨进程执行锁后，才允许清理进程意外退出留下的同一步生成尝试。
            values = snapshot.values or initial
            operation_key = f"{values['run_id']}:{values['cycle']}"
            attempt = content_generation._attempt(task)
            saved = db.scalar(select(Content.id).where(Content.task_id == task_id, Content.generation_key == operation_key))
            if (attempt.get("operation_key") == operation_key and attempt.get("status") == "running"
                    and task.status == "running" and saved is None):
                task.checkpoint = {**(task.checkpoint or {}), "content_generation": {**attempt, "status": "interrupted"}}
                task.status, task.failed_from_status = "failed", "running"
                task.error_message = "上次工作流生成尝试已中断，准备重试"
            run = _run(db, task_id)
            run.status, run.error_message = "running", None
        return _drive(db, task_id, graph, graph_config, None if snapshot.values else initial)
