"""用户可维护规则与风险提示；检查结果不决定审核或发布权限。"""
import hashlib
import json

from sqlalchemy import func, or_, select

from agents import rule_agent
from models.rules import CustomRule, PlatformRule, ContentRuleCheck
from models.content import Content
from models.task import Task
from models.workflow import TaskWorkflow
from models.user import Users
from schemas.rules import RuleCheckRead, RuleRisk
from .common import current_user_id, get_record, list_records, owned_query, write_transaction
from .common import remove_record
from .exceptions import ConflictError, InvalidInputError, PermissionDeniedError, ServiceError
from .analysis_models import select_model


RULE_FIELDS = ("content", "rule_type", "keywords", "max_length", "targets", "severity", "is_active", "source_url")


def _values(data):
    value = data.model_dump(mode="json")
    if value.get("platform_name") is not None:
        value["platform_name"] = value["platform_name"].strip().lower()
    return value


def require_admin(db):
    current_user_id(db)
    if not db.info.get("is_admin"):
        raise PermissionDeniedError("只有管理员可以维护共享平台规则，请使用自定义规则")


def _create(db, model, data):
    with write_transaction(db):
        rule = model(**_values(data))
        db.add(rule)
    return rule


def _replace(db, model, rule_id, data):
    with write_transaction(db):
        rule = get_record(db, model, rule_id, lock=True)
        values = _values(data)
        changed = any(getattr(rule, name) != value for name, value in values.items())
        for name, value in values.items():
            setattr(rule, name, value)
        if changed:
            rule.version += 1
    return rule


def create_custom_rule(db, data):
    with write_transaction(db):
        get_record(db, Users, current_user_id(db))
        rule = CustomRule(**_values(data), user_id=current_user_id(db))
        db.add(rule)
    return rule


def update_custom_rule(db, rule_id, data):
    return _replace(db, CustomRule, rule_id, data)


def get_custom_rule(db, rule_id):
    return get_record(db, CustomRule, rule_id)


def list_custom_rules(db, *, user_id=None, platform_name=None, offset=0, limit=20):
    return list_records(db, CustomRule, user_id=user_id, platform_name=platform_name, offset=offset, limit=limit)


def create_platform_rule(db, data):
    with write_transaction(db):
        rule = PlatformRule(**_values(data), user_id=current_user_id(db))
        db.add(rule)
    return _platform_read(db, rule)


def update_platform_rule(db, rule_id, data):
    with write_transaction(db):
        user = get_record(db, Users, current_user_id(db), lock=True)
        rule = get_record(db, PlatformRule, rule_id, lock=True)
        values = _values(data)
        if rule.user_id is None:
            if values["platform_name"] != rule.platform_name:
                raise InvalidInputError("跨平台规则请新建，公共参考规则的平台不能覆盖")
            previous = _platform_read(db, rule)
            user.platform_rule_overrides = {**user.platform_rule_overrides, str(rule_id): {
                **values, "version": previous["version"] + 1}}
        else:
            changed = any(getattr(rule, key) != value for key, value in values.items())
            for key, value in values.items():
                setattr(rule, key, value)
            if changed:
                rule.version += 1
    return _platform_read(db, rule)


def get_platform_rule(db, rule_id):
    return _platform_read(db, get_record(db, PlatformRule, rule_id))


def list_platform_rules(db, *, platform_name=None, offset=0, limit=20):
    return [_platform_read(db, row) for row in list_records(db, PlatformRule,
        platform_name=platform_name, offset=offset, limit=limit)]


def _platform_read(db, rule):
    value = {"id": rule.id, "platform_name": rule.platform_name, "version": rule.version,
             **{name: getattr(rule, name) for name in RULE_FIELDS}}
    if rule.user_id is None:
        user = get_record(db, Users, current_user_id(db))
        value.update((user.platform_rule_overrides or {}).get(str(rule.id), {}))
    return value


def delete_custom_rule(db, rule_id):
    remove_record(db, CustomRule, rule_id)


def delete_platform_rule(db, rule_id):
    with write_transaction(db):
        user = get_record(db, Users, current_user_id(db), lock=True)
        rule = get_record(db, PlatformRule, rule_id, lock=True)
        if rule.user_id is None:
            user.platform_rule_overrides = {**user.platform_rule_overrides, str(rule_id): {"is_deleted": True}}
        else:
            db.delete(rule)


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _rules_snapshot(db, platform):
    result = []
    for model, scope in ((PlatformRule, "platform"), (CustomRule, "custom")):
        platform_filter = func.lower(func.trim(model.platform_name)) == platform
        if model is CustomRule:
            platform_filter = or_(platform_filter, model.platform_name.is_(None))
        rows = list(db.scalars(owned_query(db, model).where(platform_filter).order_by(model.id)))
        for rule in rows:
            value = _platform_read(db, rule) if model is PlatformRule else {
                "id": rule.id, "version": rule.version, **{name: getattr(rule, name) for name in RULE_FIELDS}}
            if not value["is_active"] or (value["rule_type"] == "ai" and not (value["content"] or "").strip()):
                continue
            result.append({"scope": scope, **{key: value[key] for key in ("id", "version", *RULE_FIELDS)}})
            if len(result) > 200:
                raise InvalidInputError("适用规则超过 200 条，请精简或停用部分规则")
    return result


def _content_snapshot(content):
    return {name: getattr(content, name) for name in
            ("id", "task_id", "version", "title", "body", "tags", "image_prompt", "video_script", "image_assets", "generation_metadata")}


def _text_fields(snapshot):
    return {"title": snapshot["title"], "body": snapshot["body"], "tags": " ".join(snapshot["tags"]),
            "image_prompt": snapshot["image_prompt"] or "", "video_script": snapshot["video_script"] or "",
            "image_assets": "\n".join(item.get("prompt") or "" for item in snapshot["image_assets"])}


def _match_rules(snapshot, rules):
    fields = _text_fields(snapshot)
    folded_fields = {name: value.casefold() for name, value in fields.items()}
    risks = []
    for rule in rules:
        for field in rule["targets"]:
            if len(risks) >= 100:
                return risks
            value = fields[field]
            if rule["rule_type"] == "keyword":
                for keyword in rule["keywords"]:
                    if len(risks) >= 100:
                        return risks
                    index = folded_fields[field].find(keyword.casefold())
                    if index >= 0:
                        risks.append(RuleRisk(rule_scope=rule["scope"], rule_id=rule["id"], field=field,
                            excerpt=value[max(0, index - 80):max(0, index - 80) + 500] or value[:500],
                            severity=rule["severity"], source="keyword",
                            reason=f"命中配置关键词：{keyword}", suggestion=(rule["content"] or "核对命中表达，修改后重新检查")[:1000]))
            elif rule["rule_type"] == "length" and len(value) > rule["max_length"]:
                risks.append(RuleRisk(rule_scope=rule["scope"], rule_id=rule["id"], field=field,
                    excerpt=value[:500], severity=rule["severity"], source="length",
                    reason=f"字段长度 {len(value)} 超过配置上限 {rule['max_length']}",
                    suggestion=(rule["content"] or "精简该字段后重新检查；此上限来自已配置规则")[:1000]))
    return risks


def check_content(db, content_id: int, *, force: bool = False):
    content = get_record(db, Content, content_id)
    task = get_record(db, Task, content.task_id)
    platform = (task.platform_name or "rednote").strip().lower()
    if platform != "rednote":
        raise InvalidInputError("当前仅支持 rednote 风险检查，其他平台需单独配置")
    snapshot = _content_snapshot(content)
    rules = _rules_snapshot(db, platform)
    content_hash = _hash(snapshot)
    model_profile = select_model(db, None)
    model = model_profile.model
    rules_hash = _hash({"rules": rules, "model": model_profile.snapshot()})
    if not force:
        existing = db.scalar(owned_query(db, ContentRuleCheck).where(
            ContentRuleCheck.content_id == content_id, ContentRuleCheck.content_fingerprint == content_hash,
            ContentRuleCheck.rules_fingerprint == rules_hash, ContentRuleCheck.model == model)
            .order_by(ContentRuleCheck.id.desc()).limit(1))
        if existing is not None and existing.status in {"no_risk_detected", "risk_detected"}:
            return existing
    fields = _text_fields(snapshot)
    payload = {"platform": platform, "fields": fields, "rules": rules,
               "product_sources": (snapshot["generation_metadata"] or {}).get("sources", [])}
    if len(json.dumps(payload, ensure_ascii=False)) > 100000:
        raise InvalidInputError("文案与规则合计过长，暂不支持检查，请精简后重试")
    risks = _match_rules(snapshot, rules)
    warnings = []
    if len(risks) >= 100:
        warnings.append("确定性风险列表最多展示 100 条，请根据所选规则逐项核对")
    if not any(rule["scope"] == "platform" for rule in rules):
        warnings.append("未配置有效的平台规则，请由管理员补充后重新检查")
    if snapshot["image_assets"] or snapshot["image_prompt"] or snapshot["video_script"]:
        warnings.append("仅检查图片 Prompt 和视频脚本文本，未检查实际图片、OCR 或视频画面，需人工核对")
    db.rollback()
    error = None
    if rules:
        try:
            result = rule_agent.check_content(payload, profile=model_profile)
            by_id = {(rule["scope"], rule["id"]): rule for rule in rules}
            for risk in result.risks:
                if risk.rule_scope != "general":
                    referenced = by_id.get((risk.rule_scope, risk.rule_id))
                    if referenced is None or risk.field not in referenced["targets"]:
                        raise InvalidInputError("AI 规则检查引用了无效的规则或检查字段")
                if risk.excerpt not in fields[risk.field]:
                    raise InvalidInputError("AI 风险提示未引用实际文案片段")
            risks.extend(RuleRisk(**risk.model_dump(), source="ai") for risk in result.risks)
        except Exception as exc:
            error = str(exc) if isinstance(exc, ServiceError) else "AI 规则检查暂时不可用，请重新检查"
    status = ("error" if error else "incomplete" if not any(r["scope"] == "platform" for r in rules)
              else "risk_detected" if risks else "no_risk_detected")
    with write_transaction(db):
        get_record(db, Task, snapshot["task_id"], lock=True)
        current = get_record(db, Content, content_id)
        if (_hash(_content_snapshot(current)) != content_hash
                or _hash({"rules": _rules_snapshot(db, platform), "model": select_model(db, None).snapshot()}) != rules_hash):
            raise ConflictError("检查期间内容或规则已变化，本次结果未保存，请重新检查")
        record = ContentRuleCheck(user_id=current_user_id(db), task_id=snapshot["task_id"],
            content_id=content_id, content_version=snapshot["version"], platform_name=platform,
            content_fingerprint=content_hash, rules_fingerprint=rules_hash, rules_snapshot=rules,
            status=status, risks=[risk.model_dump(mode="json") for risk in risks], coverage_warnings=warnings,
            model=model, error_message=error)
        db.add(record)
    return record


def require_review_ready(db, task, content, *, required=False):
    """仅验证内容可提交；规则结果始终为提示，不作为审核或发布门槛。"""
    from .image_generation import generation_in_progress
    if generation_in_progress(task):
        raise ConflictError("配图正在生成，请完成后再提交审核")
    if not (content.title.strip() or content.body.strip() or content.image_assets):
        raise ConflictError("需要先保存文案或图片，再提交人工审核")


def apply_check_to_task(db, check_id: int):
    """内部工作流阶段切换，检查结果不等于平台审核通过。"""
    with write_transaction(db):
        check = get_record(db, ContentRuleCheck, check_id)
        task = get_record(db, Task, check.task_id, lock=True)
        latest = db.scalar(select(Content).where(Content.task_id == task.id).order_by(Content.version.desc()).limit(1))
        if task.status not in {"running", "pending_review"} or latest is None or latest.id != check.content_id:
            raise ConflictError("任务状态或最新内容已变化，请获取最新版本后重新检查")
        require_review_ready(db, task, latest)
        task.status = "pending_review"
    return check


def get_check(db, check_id):
    return get_record(db, ContentRuleCheck, check_id)


def list_checks(db, *, content_id=None, offset=0, limit=20):
    if content_id is not None:
        get_record(db, Content, content_id)
    return list_records(db, ContentRuleCheck, content_id=content_id, offset=offset, limit=limit)
