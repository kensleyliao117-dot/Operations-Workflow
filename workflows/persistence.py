"""官方 PostgreSQL checkpoint 存储及跨进程的单任务执行互斥。"""
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

import config
from services.exceptions import ConflictError, ServiceUnavailableError

CHECKPOINT_SCHEMA = "workflow_checkpoints"
LOCK_NAMESPACE = 1942701


def _connection():
    url = make_url(config.DATABASE_URL).set(drivername="postgresql").render_as_string(hide_password=False)
    return psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=10)


def _serializer():
    return JsonPlusSerializer(pickle_fallback=False, allowed_json_modules=[], allowed_msgpack_modules=[
        ("langgraph.types", "Interrupt"), ("langgraph.types", "Command"),
    ])


def setup_checkpoints():
    """独立初始化 checkpoint schema；不加入业务表的 Alembic 元数据。"""
    with _connection() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(CHECKPOINT_SCHEMA)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(CHECKPOINT_SCHEMA)))
        PostgresSaver(connection, serde=_serializer()).setup()


@contextmanager
def open_checkpointer(task_id: int, *, exclusive: bool = False):
    try:
        with _connection() as connection:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(CHECKPOINT_SCHEMA)))
            if exclusive:
                locked = connection.execute("SELECT pg_try_advisory_lock(%s, %s) AS locked",
                                            (LOCK_NAMESPACE, task_id)).fetchone()["locked"]
                if not locked:
                    raise ConflictError("该任务的工作流正在执行，请勿重复操作")
            # 连接关闭时释放 advisory lock；不持有业务数据库事务等待模型。
            yield PostgresSaver(connection, serde=_serializer())
    except psycopg.Error:
        raise ServiceUnavailableError("工作流存储暂时不可用，请确认已运行 python -m workflows.setup") from None
