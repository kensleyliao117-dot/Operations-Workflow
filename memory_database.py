"""独立运营经验数据库，不与业务库共用 Base、事务或外键。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import MEMORY_DATABASE_URL


class MemoryBase(DeclarativeBase):
    pass


memory_engine = create_engine(MEMORY_DATABASE_URL, pool_pre_ping=True, connect_args={"connect_timeout": 5})
MemorySession = sessionmaker(bind=memory_engine)


def get_memory_db():
    with MemorySession() as db:
        yield db
