from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from config import DATABASE_URL


# 所有数据库 Model 都继承这个 Base
class Base(DeclarativeBase):
    pass

# 创建数据库 Engine
engine = create_engine(DATABASE_URL)

Sessionlocal = sessionmaker(bind=engine)

# 每个请求单独创建 Session，避免不同请求共用数据库会话。
def get_db():
    db= Sessionlocal()
    from memory_database import MemorySession
    db.info["memory_session_factory"] = MemorySession
    try:
        
        yield db
    finally:
      
        db.close()
