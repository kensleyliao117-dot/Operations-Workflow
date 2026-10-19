"""保存和查询真实指标快照，客户端不能覆盖来源或作品归属。"""
from datetime import datetime, timezone

from collectors import rednote_metrics
from collectors.rednote import _note_url
from integrations.publishers.locking import publishing_lock
from memory.models import PostMetrics, PublishedPost
from memory.schemas import MetricValues, MetricsRead
from memory.timing import observation_timing
from services.exceptions import ConflictError, InvalidInputError
from services.common import current_user_id
from services.validation import validate_pagination
from .common import get_owned, memory_transaction, owned
from .publications import find_publication, publication_source


def _existing(db, memory_db, post_id, key):
    value = memory_db.scalar(owned(db, memory_db, PostMetrics).where(PostMetrics.idempotency_key == key))
    if value is not None and value.post_id != post_id:
        raise ConflictError("同一指标请求键不能用于不同作品")
    return value


def _save(db, memory_db, post, *, key, metrics, source, observed_at, raw=None, precision=None):
    values = MetricValues.model_validate(metrics).model_dump(exclude_none=True)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None or observed_at > datetime.now(timezone.utc):
        raise InvalidInputError("指标观测时间必须包含时区且不能晚于当前时间")
    observed = observed_at.astimezone(timezone.utc).replace(tzinfo=None)
    if post.published_at and observed < post.published_at:
        raise InvalidInputError("指标观测时间不能早于实际发布时间")
    with memory_transaction(memory_db):
        get_owned(db, memory_db, PublishedPost, post.id, lock=True)
        existing = _existing(db, memory_db, post.id, key)
        if existing is not None:
            if existing.metrics != values or existing.source != source or existing.observed_at != observed:
                raise ConflictError("同一请求键对应的指标或观测时间已变化，请使用新请求键")
            return existing
        metric = PostMetrics(post_id=post.id, user_id=current_user_id(db), idempotency_key=key,
            metrics=values, metrics_raw=raw or {}, precision=precision or {}, source=source, observed_at=observed)
        memory_db.add(metric)
    return metric


def save_manual(db, memory_db, record_id, data):
    post = find_publication(db, memory_db, record_id)
    if data.account_id != post.account_id:
        raise ConflictError("人工录入需确认作品实际绑定的 account_id")
    return _save(db, memory_db, post, key=data.idempotency_key, metrics=data.metrics.model_dump(exclude_none=True),
                 source="manual", observed_at=data.observed_at,
                 precision={k: "manual_reported" for k in data.metrics.model_dump(exclude_none=True)})


def collect(db, memory_db, record_id, data):
    post = find_publication(db, memory_db, record_id)
    private = post.content_snapshot.get("visibility") == "private"
    source = data.source if data.source != "auto" else ("rednote_creator" if private else "rednote_public")
    existing = _existing(db, memory_db, post.id, data.idempotency_key)
    if existing is not None:
        if existing.source != source:
            raise ConflictError("该请求键已用于其他指标来源")
        return existing
    if post.platform != "rednote":
        raise InvalidInputError("当前仅支持小红书指标采集")
    url = data.source_url or post.platform_post_url or f"https://www.xiaohongshu.com/explore/{post.platform_post_id}"
    canonical = _note_url(url)
    post_id = post.platform_post_id
    if post_id is None and post.platform_post_url and _note_url(post.platform_post_url):
        post_id = _note_url(post.platform_post_url).rsplit("/", 1)[-1]
    if canonical is None or canonical.rsplit("/", 1)[-1] != post_id:
        raise InvalidInputError("需要可定位已发布作品的有效帖子 ID 或完整链接")
    expected_user = post.platform_user_id
    post_pk = post.id
    expected_fingerprint = post.source_fingerprint
    db.rollback()
    memory_db.rollback()
    # 与本机发布互斥，防止共享 Chrome 切换账号或并发操作。
    with publishing_lock(db):
        result = (rednote_metrics.collect_creator_metrics(platform_post_id=post_id, platform_user_id=expected_user)
                  if source == "rednote_creator" else rednote_metrics.collect_metrics(
                      platform_post_id=post_id, source_url=url, platform_user_id=expected_user))
    current = publication_source(db, record_id)
    if (result.post_id != post_id or result.platform_user_id != expected_user
            or current["source_fingerprint"] != expected_fingerprint):
        raise ConflictError("采集结果或发布记录关联发生变化，本次指标未保存")
    post = get_owned(db, memory_db, PublishedPost, post_pk)
    if result.source != source:
        raise ConflictError("指标来源与采集方式不一致，本次指标未保存")
    return _save(db, memory_db, post, key=data.idempotency_key, metrics=result.metrics, source=result.source,
                 observed_at=result.observed_at, raw=result.metrics_raw, precision=result.precision)


def list_metrics(db, memory_db, record_id, *, offset=0, limit=20):
    validate_pagination(offset, limit)
    post = find_publication(db, memory_db, record_id)
    return [snapshot_read(row, post) for row in memory_db.scalars(owned(db, memory_db, PostMetrics).where(PostMetrics.post_id == post.id)
                .order_by(PostMetrics.observed_at.desc(), PostMetrics.id.desc()).offset(offset).limit(limit))]


def snapshot_read(row, post):
    return MetricsRead.model_validate(row).model_copy(update=observation_timing(post.published_at, row.observed_at))


def describe(db, memory_db, record_id, row):
    post = find_publication(db, memory_db, record_id)
    return snapshot_read(row, post)
