"""案例及分析的持久化工具，不执行采集或调用 AI。"""
from sqlalchemy import select

from .common import current_user_id, owned_query, get_record, list_records, write_transaction
from .exceptions import ConflictError


def create_case(db, model, data):
    with write_transaction(db):
        if db.scalar(owned_query(db, model).where(model.link == data.link)) is not None:
            raise ConflictError("该链接的案例已存在")
        case = model(**data.model_dump(mode="json"), user_id=current_user_id(db))
        db.add(case)
    return case


def update_case(db, model, case_id, data):
    with write_transaction(db):
        case = get_record(db, model, case_id, lock=True)
        duplicate = db.scalar(owned_query(db, model).where(model.link == data.link, model.id != case.id))
        if duplicate is not None:
            raise ConflictError("该链接的案例已存在")
        # 保留未传入的可选字段，只有显式传 null 时才清除已有正文。
        for name, value in data.model_dump(mode="json", exclude_unset=True).items():
            setattr(case, name, value)
    return case


def create_analysis(db, case_model, analysis_model, data):
    with write_transaction(db):
        get_record(db, case_model, data.cases_id)
        analysis = analysis_model(**data.model_dump())
        db.add(analysis)
    return analysis


def list_cases(db, model, *, offset=0, limit=20):
    return list_records(db, model, offset=offset, limit=limit)


def list_analyses(db, model, *, cases_id=None, offset=0, limit=20):
    return list_records(db, model, cases_id=cases_id, offset=offset, limit=limit)
