"""生成与规则检查结果统一提供的产品提示。"""
RULE_NOTICE = "规则检查仅提供风险提示，不能保证小红书审核通过，发布前仍需人工核对。"
GENERATION_NOTICE = "基于爆款案例生成的文案、图片或视频不保证成为爆款，需人工调整，并通过真实运营数据验证效果。"
IMAGE_NOTICE = "AI 配图为创作草稿，不保证真实还原产品外观、结构、标识或图片文字，提交审核时需逐项人工核对。"


def user_notices() -> list[str]:
    return [RULE_NOTICE, GENERATION_NOTICE]
