"""经验库独立迁移入口，不加载业务 Base。"""
from alembic import context
from sqlalchemy import create_engine, pool
from config import MEMORY_DATABASE_URL
from memory_database import MemoryBase
import memory.models


options = {"target_metadata": MemoryBase.metadata, "version_table": "memory_alembic_version"}
if context.is_offline_mode():
    context.configure(url=MEMORY_DATABASE_URL, literal_binds=True, **options)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(MEMORY_DATABASE_URL, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, **options)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
