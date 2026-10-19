"""平台入口与能力目录；未接入平台可以选择和配置，但不执行其他平台的流程。"""
PLATFORMS = (
    ("rednote", "小红书"), ("other", "其他平台"),
)


def platform_catalog():
    from .content_generation import PLATFORM_PROFILES
    # 采集、审核工作流和发布适配独立声明，新增平台时逐项接入。
    return [{"id": key, "name": name, "generation": key in PLATFORM_PROFILES,
             "collection": key == "rednote", "workflow": key == "rednote",
             "publishing": key == "rednote"} for key, name in PLATFORMS]

