"""持久化状态只保存 JSON 数据，请求 Session 通过运行上下文传递。"""
from dataclasses import dataclass
from typing import TypedDict

from sqlalchemy.orm import Session


class ContentWorkflowState(TypedDict, total=False):
    task_id: int
    user_id: int
    run_id: str
    options: dict
    cycle: int
    analysis_references: list[dict]
    content_id: int
    content_version: int
    stage: str
    rule_check: dict
    rule_check_warning: str | None
    output_mode: str
    decision: str
    review_id: int | None


@dataclass
class WorkflowContext:
    db: Session
