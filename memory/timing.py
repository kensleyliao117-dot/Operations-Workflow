"""数据库历史无时区时间按 UTC 解释，观测年龄由真实时间差计算。"""
from datetime import datetime, timezone


def utc_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def observation_timing(published_at, observed_at):
    published, observed = utc_time(published_at), utc_time(observed_at)
    elapsed = (observed - published).total_seconds() if published and observed else None
    return {"published_at": published,
            "elapsed_since_publish_seconds": int(elapsed) if elapsed is not None and elapsed >= 0 else None}
