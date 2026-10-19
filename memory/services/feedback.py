"""证据快照、AI 分析和经验版本在经验库同一事务保存。"""
from sqlalchemy import func

import config
from agents import feedback_agent
from memory.models import Feedback, OptimizationMemory, PostMetrics, PublishedPost
from services.common import current_user_id
from services.analysis_models import select_model
from services.exceptions import ConflictError, InvalidInputError, ServiceUnavailableError
from .common import fingerprint, get_owned, memory_transaction, owned
from .publications import find_publication, publication_source
from .metrics import snapshot_read
from memory.timing import utc_time


def analyze(db, memory_db, record_id, data):
    post = find_publication(db, memory_db, record_id)
    post_id, source_hash = post.id, post.source_fingerprint
    metrics = [get_owned(db, memory_db, PostMetrics, i) for i in data.metrics_ids]
    if any(row.post_id != post_id for row in metrics):
        raise InvalidInputError("所有指标快照必须属于当前发布作品")
    metrics.sort(key=lambda row: (row.observed_at, row.id))
    snapshots = [snapshot_read(row, post).model_dump(mode="json") for row in metrics]
    profile = select_model(db, None)
    model = profile.model
    evidence = {"post_fingerprint": source_hash, "model": model, "analysis_prompt_version": feedback_agent.PROMPT_VERSION,
                "metrics_snapshots": snapshots, "user_note": data.user_note, "model_profile": profile.snapshot()}
    source_fingerprint = fingerprint(evidence)
    previous = memory_db.scalar(owned(db, memory_db, Feedback).where(
        Feedback.post_id == post_id, Feedback.source_fingerprint == source_fingerprint))
    if previous is not None:
        return memory_db.scalar(owned(db, memory_db, OptimizationMemory).where(OptimizationMemory.feedback_id == previous.id))
    latest = memory_db.scalar(owned(db, memory_db, OptimizationMemory).where(OptimizationMemory.post_id == post_id)
        .order_by(OptimizationMemory.version.desc()).limit(1))
    baseline = latest.version if latest else 0
    history = latest.result if latest else None
    payload = {"platform": post.platform, "content": post.content_snapshot,
               "published_at": utc_time(post.published_at).isoformat() if post.published_at else None,
               "metrics_snapshots": snapshots, "user_note": data.user_note, "previous_experience": history}
    import json
    if len(json.dumps(payload, ensure_ascii=False)) > 60000:
        raise InvalidInputError("文案和观测合计超过60000字符，请减少快照后重试")
    db.rollback()
    memory_db.rollback()
    try:
        result = feedback_agent.analyze_feedback(payload, profile=profile)
    except config.ConfigurationError as exc:
        raise ServiceUnavailableError(str(exc)) from None
    # 远程调用后重新确认业务归属及快照，旧分析不能覆盖同时完成的新分析。
    if publication_source(db, record_id)["source_fingerprint"] != source_hash:
        raise ConflictError("分析期间发布版本关联已变化，未保存经验")
    with memory_transaction(memory_db):
        post = get_owned(db, memory_db, PublishedPost, post_id, lock=True)
        previous = memory_db.scalar(owned(db, memory_db, Feedback).where(
            Feedback.post_id == post_id, Feedback.source_fingerprint == source_fingerprint))
        if previous is not None:
            return memory_db.scalar(owned(db, memory_db, OptimizationMemory).where(OptimizationMemory.feedback_id == previous.id))
        version = memory_db.scalar(owned(db, memory_db, OptimizationMemory).with_only_columns(
            func.max(OptimizationMemory.version)).where(OptimizationMemory.post_id == post_id)) or 0
        if version != baseline:
            raise ConflictError("该作品已产生更新的经验版本，请读取后重新分析")
        feedback = Feedback(post_id=post_id, user_id=current_user_id(db), source_fingerprint=source_fingerprint,
            user_note=data.user_note, evidence_snapshot=evidence, result=result.model_dump(mode="json"), model=model)
        memory_db.add(feedback)
        memory_db.flush()
        experience = OptimizationMemory(user_id=current_user_id(db), product_id=post.product_id,
            platform=post.platform, account_id=post.account_id, post_id=post_id, feedback_id=feedback.id,
            version=version + 1, result=result.model_dump(mode="json"))
        memory_db.add(experience)
    return experience
