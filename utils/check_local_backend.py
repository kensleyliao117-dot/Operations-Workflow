"""本地启动预检：只读检查配置、后端依赖和业务数据库连接。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    try:
        import uvicorn
        import app
        from config import DATABASE_URL, require_jwt_secret
        from sqlalchemy import create_engine, text

        require_jwt_secret()
        engine = create_engine(DATABASE_URL, connect_args={'connect_timeout': 5})
        try:
            with engine.connect() as connection:
                connection.execute(text('SELECT 1'))
        finally:
            engine.dispose()
    except ModuleNotFoundError as exc:
        print(f'Missing Python dependency: {exc.name}. Install requirements.txt with this Python.', file=sys.stderr)
        return 1
    except Exception as exc:
        # 数据库及供应商异常不能输出可能包含凭据的原始消息。
        if type(exc).__name__ == 'ConfigurationError':
            print(str(exc), file=sys.stderr)
        else:
            print('Backend check failed. Check installed dependencies, DATABASE_URL and the PostgreSQL service.', file=sys.stderr)
        return 1
    print('Backend dependencies, configuration and database connection are ready.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
