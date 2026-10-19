"""初始化工作流持久化表：python -m workflows.setup。"""
from .persistence import setup_checkpoints


if __name__ == "__main__":
    setup_checkpoints()
    print("LangGraph PostgreSQL checkpoint storage is ready.")
