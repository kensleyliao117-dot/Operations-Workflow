"""固定发布文案版本与本地图片摘要，提交前再次校验。"""
import hashlib
import json
import re

from integrations.llm.image import MAX_IMAGE_BYTES, validate_png
from integrations.publishers.base import PublishPayload
from .exceptions import InvalidInputError


def snapshot(db, content, account, *, visibility="public"):
    from .image_generation import image_path
    if re.fullmatch(r"[0-9a-f]{24}", account.platform_user_id) is None:
        raise InvalidInputError("Chrome 发布账号需填写个人主页链接中的 24 位用户 ID，不能使用昵称或小红书号")
    if not 1 <= len(content.image_assets or []) <= 20:
        raise InvalidInputError("本地发布需关联 1 到 20 张已保存图片")
    paths, images = [], []
    for i, asset in enumerate(content.image_assets):
        path = image_path(db, content.id, i)
        try:
            with path.open("rb") as file:
                data = file.read(MAX_IMAGE_BYTES + 1)
            validate_png(data)
        except OSError:
            raise InvalidInputError("发布图片不可读取，请重新核对素材") from None
        paths.append(path)
        images.append({"storage_key": asset["storage_key"], "sha256": hashlib.sha256(data).hexdigest()})
    value = {"content_id": content.id, "version": content.version, "title": content.title,
             "body": content.body, "tags": content.tags or [], "images": images,
             "platform_user_id": account.platform_user_id, "visibility": visibility}
    fingerprint = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return value, fingerprint, PublishPayload(content.id, content.version, content.title, content.body,
                                             tuple(content.tags or []), tuple(paths), account.platform_user_id, visibility)
