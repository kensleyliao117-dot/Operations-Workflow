"""显式创建独立经验数据库并执行其迁移，不在应用启动时创建数据库。"""
from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

import config


def main():
    target = make_url(config.MEMORY_DATABASE_URL).set(drivername="postgresql")
    maintenance = target.set(database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(maintenance, autocommit=True, connect_timeout=5) as connection:
        exists = connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target.database,)).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(sql.Identifier(target.database)))
    command.upgrade(Config(str(config.BASE_DIR / "memory" / "alembic.ini")), "head")
    print("Operations memory database initialized")


if __name__ == "__main__":
    main()
