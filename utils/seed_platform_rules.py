"""初始化基础风险提示规则：python -m utils.seed_platform_rules。"""
from sqlalchemy import select

from database import Sessionlocal
from models.rules import PlatformRule

SOURCE_URL = "https://pgy.xiaohongshu.com/help/detail?id=1eda0a065dd894063c2e029a49e8f6a1&userType=4"
BASELINE_RULES = (
    "核对产品参数、功效和使用效果是否有资料依据，提示夸大或无依据表达。",
)


def seed_rules():
    with Sessionlocal.begin() as db:
        added = 0
        for content in BASELINE_RULES:
            existing = db.scalar(select(PlatformRule.id).where(PlatformRule.platform_name == "rednote",
                                                              PlatformRule.content == content))
            if existing is None:
                db.add(PlatformRule(platform_name="rednote", content=content, source_url=SOURCE_URL))
                added += 1
        return added


if __name__ == "__main__":
    print(f"Added {seed_rules()} baseline advisory rules; platform approval is not guaranteed.")
