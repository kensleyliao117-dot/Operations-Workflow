"""保存当前用户的小红书案例，采集和 AI 分析分别处理。"""
from datetime import datetime, timezone
import hashlib
import json

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from agents import analysis_agent
from integrations.llm import analysis_models
from collectors.base import CollectedCase
from collectors.chrome import ChromeBrowser
from collectors.rednote import RednoteCollector, _note_url
from models.rednote import RednoteCase, RednoteAnalysis, RednoteAnalysisFeedback
from schemas.rednote import (
    RednoteCaseCreate, RednoteCaseRead, RednoteCollectRequest, RednoteCollectResult, RednoteAnalysisRead,
    RednoteAnalyzeRequest, RednoteAnalysisComparison, RednoteAnalysisDifference, RednoteCaseSearchRead,
    RednoteFeedbackCreate, RednoteReviseRequest,
)
from . import cases
from .common import current_user_id, get_record, owned_query, write_transaction
from .common import remove_record
from .exceptions import ConflictError, InvalidInputError, ServiceError
from .validation import validate_pagination


def create_case(db, data):
    data = data.model_copy(update={"link": _note_url(data.link) or data.link})
    with write_transaction(db):
        previous = db.scalar(select(RednoteCase).where(RednoteCase.user_id == current_user_id(db),
            RednoteCase.link == data.link, RednoteCase.is_deleted.is_(True)).with_for_update())
        if previous is not None:
            previous.is_deleted = False
            for key, value in data.model_dump(mode="json", exclude_unset=True).items():
                setattr(previous, key, value)
            return previous
    return cases.create_case(db, RednoteCase, data)


def delete_case(db, case_id):
    remove_record(db, RednoteCase, case_id)


def save_collected_case(db, collected: CollectedCase) -> RednoteCase:
    """按用户和链接去重，保存采集快照，保留已经入库的正文。"""
    if collected.platform != "rednote":
        raise InvalidInputError("这里只接收小红书采集结果")
    if not collected.title.strip() and not collected.body.strip():
        raise InvalidInputError("采集结果没有可保存的标题或正文")
    link = _note_url(collected.source_url)
    if link is None:
        raise InvalidInputError("采集结果不是有效的小红书帖子链接")
    data = RednoteCaseCreate(name=collected.title.strip() or "无标题帖子", link=link, body=collected.body,
                             image_urls=list(collected.image_urls), metrics=collected.metrics,
                             metrics_raw=collected.metrics_raw, author_platform_id=collected.author_platform_id)
    values = data.model_dump(mode="json")
    observed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with write_transaction(db):
            case = RednoteCase(**values, user_id=current_user_id(db), collected_at=observed_at)
            db.add(case)
        return case
    except ConflictError:
        # 处理并发插入触发的唯一约束，仅查询当前用户所属的案例。
        with write_transaction(db):
            existing = db.scalar(select(RednoteCase).where(
                RednoteCase.user_id == current_user_id(db), RednoteCase.link == data.link,
            ).with_for_update().execution_options(populate_existing=True))
            if existing is None:
                raise
            existing.is_deleted = False
            # 旧案例只有标题和链接时补充缺失正文，保留已经保存的快照。
            if existing.body is None:
                existing.body = data.body
            # 保留原文快照，同时更新本次采集到的图片、互动指标和作者标识。
            for name in ("image_urls", "metrics", "metrics_raw", "author_platform_id"):
                setattr(existing, name, values[name])
            existing.collected_at = observed_at
        return existing


def _snapshot(case: RednoteCase) -> dict:
    return {name: getattr(case, name) for name in
            ("id", "name", "body", "link", "image_urls", "metrics", "metrics_raw", "author_platform_id")}


def _fingerprint(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _latest_analysis(db, case_id: int):
    return db.scalar(select(RednoteAnalysis).where(RednoteAnalysis.cases_id == case_id)
                     .order_by(RednoteAnalysis.version.desc()).limit(1))


def _history(db, case_id: int) -> dict:
    latest = _latest_analysis(db, case_id)
    count = db.scalar(select(func.count()).select_from(RednoteAnalysis).where(RednoteAnalysis.cases_id == case_id))
    return {"analysis_count": count, "already_analyzed": count > 0,
            "latest_analysis_id": latest.id if latest else None,
            "history_hint": f"该案例已有 {count} 个分析版本，可查看历史或重新分析" if count else "尚无历史分析"}


def analyze_case(db, case_id: int, data: RednoteAnalyzeRequest | None = None,
                 *, feedback_base_id: int | None = None, feedback_ids: list[int] | None = None) -> RednoteAnalysis:
    data = data or RednoteAnalyzeRequest()
    case = get_record(db, RednoteCase, case_id)
    from .analysis_models import select_model
    profile = select_model(db, data.model_id)
    snapshot = _snapshot(case)
    if not (snapshot["body"] or "").strip():
        raise InvalidInputError("案例缺少正文，先完成采集后再进行自动分析")
    if len(json.dumps(snapshot, ensure_ascii=False)) > 60000:
        raise InvalidInputError("案例资料过长，暂不支持自动分析")
    fingerprint = _fingerprint(snapshot)
    latest = _latest_analysis(db, case_id)
    baseline_version = latest.version if latest else 0
    base_id = feedback_base_id or data.base_analysis_id
    base = get_record(db, RednoteAnalysis, base_id) if base_id else (latest if data.use_history else None)
    if base is not None and base.cases_id != case_id:
        raise InvalidInputError("基础分析版本必须属于当前案例")
    previous = None if base is None else {
        "analysis_id": base.id, "version": base.version, "model": base.model,
        "result": base.result, "content": base.content if base.result is None else None,
        "new_insights": base.new_insights, "source_fingerprint": base.source_fingerprint,
    }
    base_id = base.id if base else None
    if feedback_ids is not None:
        feedback = [get_record(db, RednoteAnalysisFeedback, fid) for fid in feedback_ids]
        if any(item.analysis_id != base_id for item in feedback):
            raise InvalidInputError("所选反馈必须属于正在修订的分析版本")
    elif data.use_history:
        feedback = list(db.scalars(owned_query(db, RednoteAnalysisFeedback).where(
            RednoteAnalysisFeedback.analysis_id.in_(select(RednoteAnalysis.id).where(
                RednoteAnalysis.cases_id == case_id))).order_by(RednoteAnalysisFeedback.id.desc()).limit(20)))
        feedback.reverse()
    else:
        feedback = []
    feedback_values = [{"id": item.id, "analysis_id": item.analysis_id, "content": item.content} for item in feedback]
    db.rollback()
    result = analysis_agent.analyze_case(snapshot, profile=profile, previous_analysis=previous,
                                         user_feedback=feedback_values)
    with write_transaction(db):
        case = get_record(db, RednoteCase, case_id, lock=True)
        if _fingerprint(_snapshot(case)) != fingerprint:
            raise ConflictError("分析期间案例内容已变化，本次分析未保存，请重新分析")
        current = _latest_analysis(db, case_id)
        if (current.version if current else 0) != baseline_version:
            raise ConflictError("分析期间已产生新版本，请查看最新分析后重试")
        value = result.model_dump(mode="json", exclude={"new_insights"})
        analysis = RednoteAnalysis(cases_id=case_id, result=value, model=profile.model,
            provider=profile.provider, model_profile=profile.id, version=baseline_version + 1,
            parent_analysis_id=base_id, revision_type="feedback" if feedback_ids else ("reanalysis" if latest else "initial"),
            feedback_ids=[item["id"] for item in feedback_values], new_insights=getattr(result, "new_insights", []),
            source_fingerprint=fingerprint, content=json.dumps(value, ensure_ascii=False))
        db.add(analysis)
    return analysis


def save_feedback(db, analysis_id: int, data: RednoteFeedbackCreate):
    with write_transaction(db):
        get_record(db, RednoteAnalysis, analysis_id)
        feedback = RednoteAnalysisFeedback(analysis_id=analysis_id, user_id=current_user_id(db), content=data.content)
        db.add(feedback)
    return feedback


def list_feedback(db, analysis_id: int, *, offset=0, limit=20):
    get_record(db, RednoteAnalysis, analysis_id)
    validate_pagination(offset, limit)
    return list(db.scalars(owned_query(db, RednoteAnalysisFeedback).where(
        RednoteAnalysisFeedback.analysis_id == analysis_id).order_by(RednoteAnalysisFeedback.id.desc())
        .offset(offset).limit(limit)))


def revise_analysis(db, analysis_id: int, data: RednoteReviseRequest):
    analysis = get_record(db, RednoteAnalysis, analysis_id)
    return analyze_case(db, analysis.cases_id, RednoteAnalyzeRequest(model_id=data.model_id),
                        feedback_base_id=analysis_id, feedback_ids=data.feedback_ids)


def compare_analyses(db, left_id: int, right_id: int) -> RednoteAnalysisComparison:
    left = RednoteAnalysisRead.model_validate(get_record(db, RednoteAnalysis, left_id))
    right = RednoteAnalysisRead.model_validate(get_record(db, RednoteAnalysis, right_id))
    if left.cases_id != right.cases_id:
        raise InvalidInputError("只能对比同一案例的分析版本")
    left_value, right_value = left.result.model_dump() if left.result else {}, right.result.model_dump() if right.result else {}
    if left.result is None or right.result is None:
        left_value["content"], right_value["content"] = left.content, right.content
    left_value["new_insights"], right_value["new_insights"] = left.new_insights, right.new_insights
    differences = [RednoteAnalysisDifference(field=key, left=left_value.get(key), right=right_value.get(key))
                   for key in sorted(left_value.keys() | right_value.keys()) if left_value.get(key) != right_value.get(key)]
    same_source = (left.source_fingerprint == right.source_fingerprint
                   if left.source_fingerprint and right.source_fingerprint else None)
    return RednoteAnalysisComparison(case_id=left.cases_id, left=left, right=right, differences=differences,
        same_source=same_source, same_parent=left.parent_analysis_id == right.parent_analysis_id,
        same_feedback=left.feedback_ids == right.feedback_ids,
        comparison_hint="字段差异不等于质量高低；来源、基础版本和反馈不同也会影响结果，不能仅归因于模型")


def collect_and_save(db, data: RednoteCollectRequest) -> RednoteCollectResult:
    current_user_id(db)
    if data.url is not None and _note_url(data.url) is None:
        raise InvalidInputError("请提供小红书完整帖子网页链接")
    db.rollback()
    with ChromeBrowser() as chrome:
        collector = RednoteCollector(chrome)
        collected = collector.collect(data.url) if data.url else collector.collect_current()
    existing = db.scalar(owned_query(db, RednoteCase).where(RednoteCase.link == _note_url(collected.source_url)))
    already_saved = existing is not None
    case = save_collected_case(db, collected)
    case_value = RednoteCaseRead.model_validate(case)
    history = _history(db, case_value.id)
    latest = _latest_analysis(db, case_value.id)
    response = RednoteCollectResult(case=case_value, case_already_saved=already_saved, **history)
    if latest is not None:
        response.analysis_source_changed = (latest.source_fingerprint != _fingerprint(_snapshot(case)))
    if data.analyze:
        if latest is not None and not data.reanalyze:
            response.analysis = RednoteAnalysisRead.model_validate(latest)
            response.analysis_status = "existing"
            response.history_hint += "；已复用历史分析，重新分析需设置 reanalyze=true"
            return response
        try:
            response.analysis = RednoteAnalysisRead.model_validate(analyze_case(
                db, case_value.id, RednoteAnalyzeRequest(model_id=data.model_id)))
            response.analysis_status = "completed"
            history = _history(db, case_value.id)
            for name, value in history.items():
                setattr(response, name, value)
            response.analysis_source_changed = False
        except (ServiceError, SQLAlchemyError) as exc:
            db.rollback()
            response.analysis_status = "failed"
            response.analysis_error = str(exc) if isinstance(exc, ServiceError) else "数据库暂时不可用，分析未保存"
    return response


def update_case(db, case_id, data):
    data = data.model_copy(update={"link": _note_url(data.link) or data.link})
    return cases.update_case(db, RednoteCase, case_id, data)


def get_case(db, case_id):
    return get_record(db, RednoteCase, case_id)


def list_cases(db, *, offset=0, limit=20):
    return cases.list_cases(db, RednoteCase, offset=offset, limit=limit)


def search_cases(db, *, q=None, url=None, offset=0, limit=20):
    """按关键词或规范化链接查询当前用户的案例及分析历史概况。"""
    validate_pagination(offset, limit)
    statement = owned_query(db, RednoteCase)
    if url is not None:
        canonical = _note_url(url)
        if canonical is None:
            raise InvalidInputError("请提供小红书完整帖子网页链接")
        statement = statement.where(RednoteCase.link == canonical)
    if q:
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = "%" + escaped + "%"
        statement = statement.where(or_(RednoteCase.name.ilike(pattern, escape="\\"),
            RednoteCase.body.ilike(pattern, escape="\\"), RednoteCase.link.ilike(pattern, escape="\\")))
    count = select(func.count(RednoteAnalysis.id)).where(RednoteAnalysis.cases_id == RednoteCase.id).correlate(RednoteCase).scalar_subquery()
    latest = select(RednoteAnalysis.id).where(RednoteAnalysis.cases_id == RednoteCase.id).order_by(
        RednoteAnalysis.version.desc()).limit(1).correlate(RednoteCase).scalar_subquery()
    version = select(func.max(RednoteAnalysis.version)).where(RednoteAnalysis.cases_id == RednoteCase.id).correlate(RednoteCase).scalar_subquery()
    rows = db.execute(statement.add_columns(count, latest, version).order_by(RednoteCase.id.desc()).offset(offset).limit(limit))
    return [RednoteCaseSearchRead(**RednoteCaseRead.model_validate(case).model_dump(),
        analysis_count=n, latest_analysis_id=aid, latest_analysis_version=v, already_analyzed=n > 0,
        history_hint=f"该案例已有 {n} 个分析版本，可查看历史或重新分析" if n else "尚无历史分析")
        for case, n, aid, v in rows]


def create_analysis(db, data):
    with write_transaction(db):
        get_record(db, RednoteCase, data.cases_id, lock=True)
        latest = _latest_analysis(db, data.cases_id)
        analysis = RednoteAnalysis(**data.model_dump(), version=latest.version + 1 if latest else 1,
                                   revision_type="manual")
        db.add(analysis)
    return analysis


def get_analysis(db, analysis_id):
    return get_record(db, RednoteAnalysis, analysis_id)


def list_analyses(db, *, cases_id=None, offset=0, limit=20):
    return cases.list_analyses(db, RednoteAnalysis, cases_id=cases_id, offset=offset, limit=limit)
