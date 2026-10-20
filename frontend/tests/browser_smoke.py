"""真实 Chrome 的前端交互验收；全部 API 被拦截为契约校验替身，不调用模型或发布平台。"""
import base64
from copy import deepcopy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import unittest
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright, expect
from schemas.auth import LoginRequest, RegisterRequest
from schemas.product import ProductCreate, ProductUpdate
from schemas.task import TaskCreate
from schemas.content import ContentUpdate
from schemas.content_generation import TaskContentGenerationRequest
from schemas.image_generation import ImageGenerationRequest
from schemas.prompt import PromptCreate
from schemas.publish_record import PublishRequest, PublishExecuteRequest, PublishResult
from schemas.review import ReviewCreate
from schemas.workflow import WorkflowResumeRequest, WorkflowGenerationRequest
from schemas.rednote import RednoteAnalyzeRequest, RednoteCaseCreate
from schemas.analysis_model import AnalysisModelWrite
from schemas.resource_model import ResourceModelWrite
from schemas.rules import CustomRuleCreate
from memory.schemas import FeedbackRequest, CollectMetricsRequest, ManualMetricsRequest


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "storage" / "frontend-tests"
FRONT = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN6sAAAAASUVORK5CYII=")
NOW = "2026-10-18T12:00:00"


class ContractAPI:
    def __init__(self, ready=False):
        self.calls = []
        self.errors = []
        self.save_failure = False
        self.expired = False
        self.user = {"id": 101, "username": "ui_operator", "email": None}
        self.products = [{"id": 11, "user_id": 101, "name": "演示耳机", "description": "通勤场景资料", "created_at": NOW}]
        self.docs = [{"id": 12, "product_id": 11, "filename": "产品手册.txt", "size_bytes": 12, "content_type": "text/plain", "status": "indexed", "created_at": NOW}]
        self.prompts = []
        self.rules = []
        self.platform_rules = []
        self.check_status = 'no_risk_detected'
        self.case_deleted = False
        self.workflow_stage_override = None
        self.workflow_content_id = 41 if ready else None
        self.task = {"id": 31, "user_id": 101, "product_id": 11, "platform_name": "rednote", "task_type": "generate", "input_text": "通勤场景介绍", "status": "pending_review" if ready else "pending", "checkpoint": None, "failed_from_status": None, "error_message": None, "created_at": NOW, "updated_at": NOW}
        self.created = ready
        self.started = ready
        self.contents = [self.content(41, 1)] if ready else []
        self.reviews = []
        self.records = []
        self.metrics = []
        self.experiences = []
        self.metric_keys = {}
        self.feedback_cache = {}
        self.feedback_evidence = {}
        self.analysis = {"id": 81, "cases_id": 71, "version": 1, "model": "test-model", "result": {"summary": "先从通勤场景切入，避免照搬产品事实", "hook": "场景切入", "reusable_patterns": ["场景后接需求"]}, "content": "分析示例", "created_at": NOW}
        self.analyses = [self.analysis]
        self.models = [{"id": "test-model", "model": "test-model", "name": "Default analysis", "provider": "deepseek", "ready": True, "is_active": True, "source": "system", "config_id": None}]
        self.system_models = deepcopy(self.models)
        self.resource_models = []
        self.case = {"id": 71, "name": "通勤参考案例", "body": "公共案例原文示例", "link": "https://www.xiaohongshu.com/explore/" + "3" * 24, "image_urls": [], "metrics": {"likes": 20, "comments": None, "favorites": 8}, "already_analyzed": True, "latest_analysis_id": 81, "latest_analysis_version": 1, "analysis_count": 1, "history_hint": "已有 1 个分析版本"}

    def content(self, id, version):
        return {"id": id, "task_id": 31, "title": "通勤耳机资料示例", "body": "这是虚构产品的流程演示文案。", "tags": ["流程测试"], "version": version, "image_assets": [], "image_prompt": None, "video_script": None, "generation_metadata": {"prompt_id": 21, "analysis_references": [{"analysis_id": 81}], "sources": [], "memory_status": "no_matches"}, "created_at": NOW}

    def check(self, id):
        c = next(c for c in self.contents if c["id"] == id)
        risks = [] if self.check_status != 'risk_detected' else [{'field': 'body', 'severity': 'medium', 'reason': '需要人工核对的提示', 'excerpt': c['body'], 'suggestion': '自行判断是否调整'}]
        return {"id": id, "content_id": id, "content_version": c["version"], "task_id": 31, "status": self.check_status, "risks": risks, "coverage_warnings": [], "image_binary_reviewed": False}

    def workflow(self):
        c = next((c for c in self.contents if c['id'] == self.workflow_content_id), None)
        state = "approved" if self.task["status"] in {"approved", "publishing", "completed"} else 'waiting_changes' if self.task['status'] == 'rejected' else self.workflow_stage_override or "waiting_review"
        return {"task_id": 31, "run_id": "test-run", "task_status": self.task["status"], "status": state, "stage": state, "content_id": c["id"] if c else None, "content_version": c["version"] if c else None, "rule_check": self.check(c["id"]) if c else None, "next_nodes": ["human_review"] if state != "approved" else [], "interrupt": {}, "error_message": None, "notices": []}

    def handle(self, route):
        request = route.request
        path = urlsplit(request.url).path
        params = parse_qs(urlsplit(request.url).query)
        method = request.method
        headers = {"Access-Control-Allow-Origin": FRONT, "Access-Control-Allow-Headers": "Authorization, Content-Type", "Access-Control-Allow-Methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS"}
        if method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
            return
        data = request.post_data_json if request.headers.get("content-type", "").startswith("application/json") else None
        self.calls.append((method, path, deepcopy(data), request.headers))
        status, result = 200, None
        try:
            if self.expired and not path.startswith("/auth/login"):
                status, result = 401, {"detail": "会话已失效"}
            elif path == "/auth/login":
                LoginRequest.model_validate(data); result = {"access_token": "isolated-ui-test-token", "expires_in": 3600, "token_type": "bearer"}
            elif path == "/auth/register":
                RegisterRequest.model_validate(data); status, result = 201, self.user
            elif path == "/auth/me": result = self.user
            elif path == "/platforms": result = [{"id": key, "name": name, "workflow": key == "rednote", "generation": key == "rednote", "collection": key == "rednote", "publishing": key == "rednote"} for key, name in [("rednote", "小红书"), ("other", "其他平台")]]
            elif path == '/resource-models' and method == 'GET':
                kind = parse_qs(urlsplit(request.url).query).get('kind', [None])[0]
                result = [row for row in self.resource_models if kind is None or row['kind'] == kind]
            elif path == '/resource-models' and method == 'POST':
                parsed = ResourceModelWrite.model_validate(data)
                value = {**parsed.model_dump(exclude={'api_key'}), 'id': 101 + len(self.resource_models), 'ready': parsed.is_active}
                if parsed.is_default:
                    for row in self.resource_models:
                        if row['kind'] == parsed.kind: row['is_default'] = False
                self.resource_models.append(value); status, result = 201, value
            elif path.startswith('/resource-models/') and method == 'PUT':
                parsed = ResourceModelWrite.model_validate(data)
                row = next(row for row in self.resource_models if row['id'] == int(path.rsplit('/', 1)[-1]))
                row.update(parsed.model_dump(exclude={'api_key'})); result = row
            elif path.startswith('/resource-models/') and method == 'DELETE':
                self.resource_models = [row for row in self.resource_models if row['id'] != int(path.rsplit('/', 1)[-1])]; status = 204
            elif path.startswith('/resource-models/') and path.endswith('/test') and method == 'POST':
                result = {'ok': True, 'model': 'custom-embedding', 'message': 'Embedding 接口与向量维度验证成功'}
            elif path == "/analysis-models" and method == "GET": result = self.models
            elif path == "/analysis-models" and method == "POST":
                AnalysisModelWrite.model_validate(data)
                value = {**{k: v for k, v in data.items() if k != 'api_key'}, "id": "user:91", "config_id": 91, "source": "user", "credential_source": "user", "ready": data["is_active"]}
                self.models.append(value); status, result = 201, value
            elif path == "/analysis-models/91" and method == "PUT":
                AnalysisModelWrite.model_validate(data); self.models[-1].update(**{k: v for k, v in data.items() if k != 'api_key'}, ready=data["is_active"]); result = self.models[-1]
            elif path == "/analysis-models/91/test" and method == "POST":
                result = {"ok": True, "model": self.models[-1]['model'], "message": "模型连接成功，已返回有效文字"}
            elif path == "/analysis-models/91" and method == "DELETE": self.models = [row for row in self.models if row['id'] != 'user:91']; status = 204
            elif path == "/analysis-models/system/test-model" and method == "DELETE": self.models = [row for row in self.models if row['id'] != 'test-model']; status = 204
            elif path == "/analysis-models/system/restore" and method == "POST":
                self.models = deepcopy(self.system_models) + [row for row in self.models if row['source'] == 'user']; status = 204
            elif path == "/auth/logout": status = 204
            elif path == "/products" and method == "GET": result = self.products
            elif path == "/products" and method == "POST":
                ProductCreate.model_validate(data); value = {**data, "id": 11, "user_id": 101, "created_at": NOW}; self.products.append(value); status, result = 201, value
            elif path == "/products/11" and method == "PATCH":
                ProductUpdate.model_validate(data); self.products[0].update(data); result = self.products[0]
            elif path == "/products/11" and method == "DELETE": self.products = []; status = 204
            elif path == "/products/11/documents" and method == "GET": result = self.docs
            elif path == "/products/11/documents" and method == "POST":
                assert "multipart/form-data" in request.headers["content-type"]
                value = {**self.docs[0], "id": 13, "filename": "补充资料.txt", "status": "uploaded"}; self.docs.append(value); status, result = 201, value
            elif path.endswith("/rag/documents/13/index"):
                self.docs[-1]["status"] = "indexed"; result = {"product_id": 11, "document_id": 13, "status": "indexed", "chunk_count": 1}
            elif path == "/prompts" and method == "GET": result = [p for p in self.prompts if p["platform"] == params.get("platform", [p["platform"]])[0]]
            elif path == "/prompts" and method == "POST":
                PromptCreate.model_validate(data); value = {**data, "id": 21, "version": 1, "user_id": 101}; self.prompts.append(value); status, result = 201, value
            elif path == '/prompts/21' and method == 'PUT':
                PromptCreate.model_validate(data); self.prompts[0].update(**data, version=self.prompts[0]['version'] + 1); result = self.prompts[0]
            elif path == '/prompts/21' and method == 'DELETE': self.prompts = []; status = 204
            elif path == "/tasks" and method == "GET": result = [self.task] if self.created and params.get("platform_name", [self.task["platform_name"]])[0] == self.task['platform_name'] else []
            elif path == "/tasks" and method == "POST":
                TaskCreate.model_validate(data); assert "user_id" not in data
                self.task.update(data); self.created = True; status, result = 201, self.task
            elif path == "/tasks/31" and method == 'DELETE':
                self.created = False
                status, result = 204, None
            elif path == "/tasks/31": result = self.task
            elif path == "/tasks/31/workflow" and not self.started: status, result = 404, {"detail": "尚未启动工作流"}
            elif path == "/tasks/31/workflow": result = self.workflow()
            elif path == "/tasks/31/workflow/start":
                options = WorkflowGenerationRequest.model_validate(data); self.started = True; self.contents = [self.content(41, 1)]
                self.contents[0]['generation_metadata']['output_mode'] = options.output_mode
                if options.output_mode != 'text':
                    if options.output_mode == 'images': self.contents[0].update(title='', body='', tags=[])
                    value = {**deepcopy(self.contents[0]), 'id': 42, 'version': 2, 'image_assets': [{'storage_key': 'generated_images/test.png', 'prompt': 'Test image'}]}
                    self.contents.insert(0, value)
                self.workflow_content_id = self.contents[0]['id']; self.task["status"] = "pending_review"; status, result = 201, self.workflow()
                self.contents[0]["generation_metadata"].update(prompt_id=data.get("prompt_id"), analysis_references=[{"analysis_id": id} for id in data.get("analysis_ids", [])])
            elif path == "/tasks/31/workflow/resume":
                WorkflowResumeRequest.model_validate(data)
                if data['action'] == 'submit_changes':
                    old = next(c for c in self.contents if c['id'] == self.workflow_content_id)
                    assert self.contents[0]['version'] > old['version'], 'submit_changes requires a newly saved version'
                    self.workflow_content_id = self.contents[0]['id']
                if data['action'] in {'submit_review', 'recheck'}:
                    assert self.workflow_content_id == self.contents[0]['id'], 'same-version operation requires the workflow version'
                if data["action"] in {"submit_changes", "submit_review", "recheck"}:
                    self.task["status"] = "pending_review"; self.workflow_stage_override = None
                result = self.workflow()
            elif path == "/contents" and method == "GET": result = self.contents
            elif re.fullmatch(r"/contents/\d+", path) and method == "PATCH":
                ContentUpdate.model_validate(data)
                if self.save_failure: route.abort("failed"); return
                old = self.contents[0]; assert path == f"/contents/{old['id']}"
                value = {**deepcopy(old), **data, "id": old["id"] + 1, "version": old["version"] + 1}; self.contents.insert(0, value); self.task["status"] = "running"; status, result = 201, value
            elif re.fullmatch(r"/contents/\d+/images/generate", path):
                ImageGenerationRequest.model_validate(data); old = self.contents[0]
                value = {**deepcopy(old), "id": old["id"] + 1, "version": old["version"] + 1, "image_assets": [{"storage_key": "generated_images/test.png", "url": None, "prompt": "测试配图"}]}; self.contents.insert(0, value); self.workflow_content_id = value['id']; self.task["status"] = "pending_review"
                status, result = 201, {"content": value, "review_status": "waiting_review", "workflow": self.workflow()}
            elif re.fullmatch(r"/contents/\d+/images/0", path):
                assert request.headers.get("authorization") == "Bearer isolated-ui-test-token"
                route.fulfill(status=200, headers=headers, content_type="image/png", body=PNG); return
            elif path == "/reviews" and method == "GET": result = self.reviews
            elif path == "/reviews" and method == "POST":
                ReviewCreate.model_validate(data); assert data["content_id"] == self.contents[0]["id"]
                value = {**data, "id": 91, "reviewer_id": 101, "created_at": NOW}; self.reviews.append(value); self.task["status"] = data["status"]; status, result = 201, value
            elif path == "/rules/checks": result = [self.check(int(params["content_id"][0]))]
            elif path == '/rules/custom' and method == 'POST':
                CustomRuleCreate.model_validate(data); result = {**data, 'id': 101, 'version': 1}; self.rules.append(result); status = 201
            elif path == '/rules/custom': result = [r for r in self.rules if r['platform_name'] == params.get('platform_name', [r['platform_name']])[0]]
            elif path == '/rules/custom/101' and method == 'PUT': self.rules[0].update(**data, version=2); result = self.rules[0]
            elif path == '/rules/custom/101' and method == 'DELETE': self.rules = []; status = 204
            elif path == '/rules/platform': result = self.platform_rules
            elif path == '/rules/platform/201' and method == 'PUT': self.platform_rules[0].update(**data, version=2); result = self.platform_rules[0]
            elif path == '/rules/platform/201' and method == 'DELETE': self.platform_rules = []; status = 204
            elif path == '/rednote/cases' and method == 'POST':
                RednoteCaseCreate.model_validate(data); self.case.update(data); self.case_deleted = False; status, result = 201, self.case
            elif path == '/rednote/cases/71' and method == 'PUT':
                RednoteCaseCreate.model_validate(data); self.case.update(data); result = self.case
            elif path == '/rednote/cases/71' and method == 'DELETE': self.case_deleted = True; status = 204
            elif path == "/rednote/cases/search": result = [] if self.case_deleted else [self.case]
            elif path == "/rednote/analyses": result = self.analyses
            elif re.fullmatch(r"/rednote/analyses/\d+", path): result = next(a for a in self.analyses if a["id"] == int(path.rsplit("/", 1)[-1]))
            elif path == "/rednote/cases/71/analyze":
                RednoteAnalyzeRequest.model_validate(data)
                chosen = next(m for m in self.models if m['id'] == data.get('model_id', 'test-model'))
                value = {**deepcopy(self.analysis), "id": 82, "version": 2, "model": chosen['model']}
                self.analyses.insert(0, value); self.case.update(latest_analysis_id=82, latest_analysis_version=2, analysis_count=2)
                status, result = 201, value
            elif path == "/rednote/analysis-models": result = self.models
            elif path == "/platform-accounts": result = [{"id": 61, "user_id": 101, "platform": "rednote", "platform_user_id": "2" * 24, "display_name": "演示发布账号", "is_active": True}]
            elif path == "/publish-records" and method == "GET": result = self.records
            elif path == "/publish-records" and method == "POST":
                PublishRequest.model_validate(data); assert data["content_id"] == self.contents[0]["id"]; assert self.task["status"] == "approved"
                self.task["status"] = "publishing"; value = {**data, "id": 51, "publish_status": "publishing", "content_version": self.contents[0]["version"], "delivery_phase": "prepared", "platform_post_url": None, "platform_post_id": None, "error_message": None, "published_at": None}; self.records = [value]; status, result = 201, value
            elif path == "/publish-records/51/execute":
                PublishExecuteRequest.model_validate(data); self.records[0].update(publish_status="unknown", delivery_phase="awaiting_confirmation", error_message="测试：平台结果不明，请核实"); result = self.records[0]
            elif path == "/publish-records/51/result":
                PublishResult.model_validate(data); self.records[0].update(publish_status="published", delivery_phase="finished", platform_post_id="1" * 24, platform_post_url=data["platform_post_url"], error_message=None, published_at="2026-10-15T12:00:00Z", publication_details={'final_content': data.get('final_content'), 'external_materials_note': data.get('external_materials_note')}); self.task["status"] = "completed"; result = self.records[0]
            elif path == '/memory/publications':
                result = [{**r, 'product_id': 11, 'product_name': '演示耳机', 'title': self.contents[0]['title']} for r in self.records if r['publish_status'] == 'published' and r['platform'] == params.get('platform', [r['platform']])[0]]
            elif path.endswith("/memory/sync-status"): result = {"status": "synced", "publish_record_id": 51, "attempts": 1, "error_message": None}
            elif path.endswith("/memory/publication"): result = {"id": 1, "publish_record_id": 51, "published_at": self.records[0]['published_at']}
            elif path.endswith('/memory/metrics') and method == 'POST':
                ManualMetricsRequest.model_validate(data)
                key = data['idempotency_key']
                if key not in self.metric_keys:
                    elapsed = int((datetime.fromisoformat(data['observed_at'].replace('Z', '+00:00')) - datetime.fromisoformat(self.records[0]['published_at'].replace('Z', '+00:00'))).total_seconds())
                    value = {'id': len(self.metrics) + 1, 'metrics': data['metrics'], 'observed_at': data['observed_at'], 'created_at': NOW, 'source': 'manual', 'published_at': self.records[0]['published_at'], 'elapsed_since_publish_seconds': elapsed}
                    self.metrics.insert(0, value); self.metric_keys[key] = value
                status, result = 201, self.metric_keys[key]
            elif path.endswith("/memory/metrics"): result = self.metrics
            elif path == "/memory/experiences": result = self.experiences
            elif path.endswith("/memory/collect"):
                CollectMetricsRequest.model_validate(data)
                key = data['idempotency_key']
                if key not in self.metric_keys:
                    elapsed = 7200 if not self.metrics else 86400
                    observed = datetime.fromisoformat(self.records[0]['published_at'].replace('Z', '+00:00')) + timedelta(seconds=elapsed)
                    value = {'id': len(self.metrics) + 1, 'metrics': {'views': 10 * (len(self.metrics) + 1), 'likes': 0, 'comments': 0, 'favorites': 0}, 'observed_at': observed.isoformat(), 'created_at': NOW, 'source': data.get('source', 'rednote_creator'), 'published_at': self.records[0]['published_at'], 'elapsed_since_publish_seconds': elapsed}
                    self.metrics.insert(0, value); self.metric_keys[key] = value
                status, result = 201, self.metric_keys[key]
            elif path.endswith("/memory/analyze"):
                FeedbackRequest.model_validate(data)
                key = json.dumps(data, sort_keys=True)
                if key not in self.feedback_cache:
                    version = len(self.experiences) + 1
                    value = {'id': version, 'version': version, 'post_id': 1, 'feedback_id': version, 'product_id': 11, 'is_active': True, 'created_at': NOW, 'result': {'summary': '根据真实观测提出下一轮待验证建议，不保证效果。', 'lessons': [{'category': 'hook', 'confidence': 'low', 'conclusion_type': 'hypothesis', 'observation': '本篇作品在不同观察窗口保留了真实数据。', 'next_experiment': '保持产品事实一致，比较相同观察周期的不同开头。', 'evidence_metric_ids': data['metrics_ids']}], 'limitations': ['单篇作品的累计观测，不能作因果结论']}}
                    self.experiences.insert(0, value); self.feedback_cache[key] = value
                    self.feedback_evidence[version] = {'model': 'fixture', 'created_at': NOW, 'evidence_snapshot': {'metrics_snapshots': deepcopy([m for m in self.metrics if m['id'] in data['metrics_ids']])}}
                status, result = 201, self.feedback_cache[key]
            elif re.fullmatch(r'/memory/feedback/\d+', path): result = self.feedback_evidence[int(path.rsplit('/', 1)[-1])]
            elif re.fullmatch(r'/memory/experiences/\d+', path) and method == 'PATCH':
                result = next(e for e in self.experiences if e['id'] == int(path.rsplit('/', 1)[-1])); result.update(data)
            else: raise AssertionError(f"Undefined test API: {method} {path}")
            route.fulfill(status=status, headers=headers, content_type="application/json", body="" if status == 204 else json.dumps(result, ensure_ascii=False))
        except Exception as exc:
            self.errors.append(str(exc)); route.fulfill(status=500, headers=headers, content_type="application/json", body=json.dumps({"detail": str(exc)}))


class FrontendBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe", headless=True, downloads_path=str(ARTIFACTS / "downloads"), args=["--no-proxy-server"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.runtime.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1512, "height": 1100}, locale="zh-CN")
        self.page = self.context.new_page(); self.page.set_default_timeout(12000)
        self.fixture = ContractAPI()
        self.page.route(API + "/**", self.fixture.handle)
        self.runtime_errors = []
        self.page.on("pageerror", lambda error: self.runtime_errors.append(str(error)))
        self.dialog_handler = lambda dialog: dialog.accept()
        self.page.on("dialog", self.dialog_handler)

    def tearDown(self):
        self.page.evaluate("window.scrollTo(0,0)")
        self.page.screenshot(path=str(ARTIFACTS / (self._testMethodName + ".png")), full_page=True)
        self.context.close()
        self.assertEqual(self.runtime_errors, [])
        self.assertEqual(self.fixture.errors, [])

    def login(self, target="/products"):
        self.page.goto(FRONT + target)
        expect(self.page.get_by_role("heading", name="登录工作台")).to_be_visible()
        self.page.get_by_label("用户名", exact=True).fill("ui_operator")
        self.page.get_by_label("密码", exact=True).fill("test-password-2026")
        self.page.get_by_role("button", name="登录", exact=True).click()
        expect(self.page.locator(".topbar")).to_contain_text("ui_operator")

    def calls(self, method, path): return [c for c in self.fixture.calls if c[:2] == (method, path)]

    def test_task_deletion_requires_confirmation_and_clears_current_selection(self):
        self.fixture.created = True
        self.login('/generate?task_id=31')
        p = self.page
        p.get_by_role('button', name='任务历史', exact=True).click()
        p.get_by_role('button', name='删除任务 #31', exact=True).click()
        dialog = p.get_by_role('dialog', name='删除任务', exact=True)
        dialog.get_by_role('button', name='取消', exact=True).click()
        self.assertEqual(len(self.calls('DELETE', '/tasks/31')), 0)
        p.get_by_role('button', name='删除任务 #31', exact=True).click()
        dialog.get_by_role('button', name='确认删除', exact=True).click()
        expect(p.get_by_role('button', name='创建任务', exact=True)).to_be_visible()
        self.assertEqual(len(self.calls('DELETE', '/tasks/31')), 1)
        p.get_by_role('button', name='任务历史', exact=True).click()
        expect(p.get_by_role('button', name='删除任务 #31', exact=True)).to_have_count(0)
        p.reload()
        expect(p.get_by_role('button', name='创建任务', exact=True)).to_be_visible()

    def test_single_main_area_navigation_restores_original_palette_and_sidebar_preference(self):
        p = self.page; self.login('/products')
        self.assertEqual(p.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--color-primary').trim()"), '#315c4b')
        self.assertEqual(p.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--color-bg').trim()"), '#f5f6f2')
        self.assertEqual(p.get_by_label('选择产品资料').count(), 0)
        self.assertGreater(p.locator('.product-library').bounding_box()['width'], 850)
        p.screenshot(path=str(ARTIFACTS / 'products-list.png'), full_page=True)
        p.get_by_role('button', name=re.compile('演示耳机')).click()
        expect(p).to_have_url(FRONT + '/products/11')
        expect(p.get_by_label('选择产品资料')).to_be_visible()
        self.assertEqual(p.locator('.product-library').count(), 0)
        p.get_by_role('link', name='案例库', exact=True).click()
        expect(p.get_by_role('heading', name='案例库', exact=True)).to_be_visible()
        expect(p.get_by_label('分析模型', exact=True)).to_be_visible()
        p.screenshot(path=str(ARTIFACTS / 'cases.png'), full_page=True)
        p.get_by_role('link', name='内容创作', exact=True).click()
        expect(p.get_by_label('本次运营需求')).to_be_visible()
        self.assertEqual(p.get_by_label('分析模型', exact=True).count(), 0)
        self.assertGreater(p.get_by_label('本次运营需求').bounding_box()['width'], 750)
        p.get_by_role('button', name='选择参考案例', exact=True).click()
        p.get_by_role('dialog').get_by_label('搜索参考案例').fill('通勤')
        p.get_by_role('dialog').get_by_label('搜索参考案例').press('Enter')
        p.get_by_role('dialog').get_by_role('button', name='关闭弹窗').click()
        self.assertEqual(len(self.calls('POST', '/tasks')), 0)
        p.screenshot(path=str(ARTIFACTS / 'create-empty.png'), full_page=True)
        toggle = p.get_by_role('button', name='切换侧栏', exact=True)
        toggle.click(); expect(toggle).to_have_attribute('aria-expanded', 'false')
        expect(p.locator('#workspace-sidebar')).to_have_attribute('inert', '')
        p.reload(); expect(p.get_by_role('button', name='切换侧栏')).to_have_attribute('aria-expanded', 'false')

    def test_settings_pages_edit_platform_configuration_without_modals(self):
        p = self.page; self.login('/settings')
        expect(p.get_by_role('heading', name='设置', exact=True)).to_be_visible()
        expect(p.get_by_label('模型 ID', exact=True)).to_be_visible()
        self.assertEqual(p.get_by_role('dialog').count(), 0)
        p.screenshot(path=str(ARTIFACTS / 'settings.png'), full_page=True)
        p.get_by_role('link', name='Prompt 模板', exact=True).click()
        p.get_by_label('模板名称').fill('创作风格')
        p.get_by_label('Prompt 内容').fill('先写场景，再介绍事实。')
        p.get_by_role('button', name='保存模板', exact=True).click()
        expect(p.get_by_label('选择模板')).to_have_value('21')
        p.get_by_role('link', name='平台规则', exact=True).click()
        p.get_by_label('规则说明').fill('核对产品参数来源')
        p.get_by_role('button', name='保存规则', exact=True).click()
        expect(p.get_by_label('已有规则').locator('option')).to_have_count(2)
        self.assertEqual(self.calls('POST', '/rules/custom')[-1][2]['platform_name'], 'rednote')
        p.set_viewport_size({'width': 390, 'height': 844})
        self.assertLessEqual(p.evaluate('document.documentElement.scrollWidth'), 390)

    def test_leaving_product_detail_warns_before_discarding_queued_files(self):
        p = self.page; self.login('/products/11')
        p.get_by_label('选择产品资料').set_input_files({'name': '待上传.txt', 'mimeType': 'text/plain', 'buffer': b'draft'})
        p.remove_listener('dialog', self.dialog_handler)
        cancel = lambda dialog: dialog.dismiss()
        p.on('dialog', cancel)
        p.get_by_role('link', name='案例库', exact=True).click()
        expect(p).to_have_url(FRONT + '/products/11')
        expect(p.get_by_text('待上传.txt', exact=False)).to_be_visible()
        self.assertEqual(len(self.calls('POST', '/products/11/documents')), 0)
        p.remove_listener('dialog', cancel); p.on('dialog', self.dialog_handler)
        p.get_by_role('link', name='案例库', exact=True).click()
        expect(p.get_by_role('heading', name='案例库', exact=True)).to_be_visible()

    def choose_reference(self, version=1):
        p = self.page
        p.get_by_role('button', name='选择参考案例', exact=True).click()
        dialog = p.get_by_role('dialog')
        dialog.get_by_label(f'引用案例：通勤参考案例 v{version}', exact=True).check()
        dialog.get_by_role('button', name=re.compile('^完成选择')).click()

    def test_background_generation_completion_keeps_new_creation_open(self):
        p = self.page; self.login('/generate')
        p.get_by_label('选择产品', exact=True).select_option('11')
        p.get_by_label('本次运营需求').fill('正在生成的旧任务')
        p.get_by_role('button', name='创建任务', exact=True).click()
        expect(p.get_by_role('button', name='启动文案生成', exact=True)).to_be_enabled()
        held = []
        def delay(route):
            if route.request.method == 'OPTIONS': self.fixture.handle(route)
            else: held.append(route)
        p.route(API + '/tasks/31/workflow/start', delay)
        p.get_by_role('button', name='启动文案生成', exact=True).click()
        expect(p.get_by_role('button', name='工作流执行中…', exact=True)).to_be_visible()
        p.get_by_role('button', name='新建内容', exact=True).click()
        expect(p.get_by_label('本次运营需求')).to_have_value('')
        p.get_by_label('本次运营需求').fill('新任务的独立草稿')
        self.assertEqual(len(held), 1)
        self.fixture.handle(held[0])
        expect(p.get_by_role('status').filter(has_text='任务 #31 工作流已返回')).to_be_visible()
        expect(p.get_by_label('本次运营需求')).to_have_value('新任务的独立草稿')
        expect(p.get_by_role('button', name='创建任务', exact=True)).to_be_visible()
        self.assertNotIn('task_id', urlsplit(p.url).query)

    def test_reanalysis_preserves_selected_version_until_explicit_switch(self):
        p = self.page; self.login("/generate")
        p.get_by_label("选择产品", exact=True).select_option("11")
        p.get_by_label("本次运营需求").fill("参考案例结构，使用本产品资料")
        self.choose_reference()
        expect(p.get_by_role("button", name=re.compile('已选分析 #81'))).to_be_visible()
        p.get_by_role('link', name='案例库', exact=True).click()
        p.get_by_role("button", name="重新分析 · 新版本", exact=True).click()
        expect(p.get_by_label("分析版本", exact=True)).to_have_value("82")
        p.get_by_role('link', name='内容创作', exact=True).click()
        expect(p.get_by_role("button", name=re.compile('已选分析 #81'))).to_be_visible()
        p.get_by_role('button', name='选择参考案例', exact=True).click()
        dialog = p.get_by_role('dialog')
        dialog.get_by_label('引用案例：通勤参考案例 v1', exact=True).uncheck()
        dialog.get_by_label('引用案例：通勤参考案例 v2', exact=True).check()
        dialog.get_by_role('button', name=re.compile('^完成选择')).click()
        expect(p.get_by_role("button", name=re.compile('已选分析 #82'))).to_be_visible()
        p.get_by_role("button", name="创建任务", exact=True).click()
        p.get_by_role("button", name="启动文案生成", exact=True).click()
        expect(p.get_by_role("link", name="前往审核 →")).to_be_visible()
        self.assertEqual(self.calls("POST", "/tasks/31/workflow/start")[0][2]["analysis_ids"], [82])

    def test_registration_validates_confirmation_and_does_not_log_in_automatically(self):
        p = self.page; p.goto(FRONT + "/register")
        p.get_by_label("用户名", exact=True).fill("ui_operator")
        p.get_by_label("密码", exact=True).fill("test-password-2026")
        p.get_by_label("确认密码", exact=True).fill("different-password")
        p.get_by_role("button", name="注册账号", exact=True).click()
        expect(p.get_by_role("alert")).to_have_text("两次输入的密码不一致")
        self.assertEqual(len(self.calls("POST", "/auth/register")), 0)
        p.get_by_label("确认密码", exact=True).fill("test-password-2026")
        p.get_by_role("button", name="注册账号", exact=True).click()
        expect(p.get_by_role("heading", name="登录工作台")).to_be_visible()
        self.assertEqual(len(self.calls("POST", "/auth/register")), 1)
        self.assertEqual(len(self.calls("POST", "/auth/login")), 0)

    def test_feedback_keeps_multiple_observation_windows_and_versions_of_evidence(self):
        self.fixture = ContractAPI(ready=True)
        self.fixture.task['status'] = 'completed'
        self.fixture.records = [{'id': 51, 'task_id': 31, 'content_id': 41, 'content_version': 1, 'account_id': 61, 'platform': 'rednote', 'publish_status': 'published', 'visibility': 'public', 'published_at': '2026-10-15T12:00:00Z', 'platform_post_id': '1' * 24, 'platform_post_url': 'https://www.xiaohongshu.com/explore/' + '1' * 24}]
        self.page.unroute(API + '/**'); self.page.route(API + '/**', self.fixture.handle)
        p = self.page; self.login('/feedback')
        p.get_by_role('button', name=re.compile('通勤耳机资料示例')).click()
        expect(p.get_by_role('heading', name='数据反馈与运营经验', exact=True)).to_be_visible()
        expect(p.get_by_label('反馈数据来源', exact=True)).to_have_value('rednote_creator')
        p.get_by_role('button', name='采集当前真实指标', exact=True).click()
        expect(p.locator('.snapshot-card')).to_have_count(1)
        expect(p.locator('.snapshot-card').first).to_contain_text('发布后 2 小时')
        self.assertEqual(self.calls('POST', '/publish-records/51/memory/collect')[-1][2]['source'], 'rednote_creator')
        p.get_by_role('button', name='采集当前真实指标', exact=True).click()
        expect(p.locator('.snapshot-card')).to_have_count(2)
        expect(p.locator('.snapshot-card').first).to_contain_text('发布后 1 天')
        keys = [c[2]['idempotency_key'] for c in self.calls('POST', '/publish-records/51/memory/collect')]
        self.assertEqual(len(set(keys)), 2)
        p.get_by_text('录入真实后台数据', exact=True).click()
        p.get_by_label('曝光量（未知留空）', exact=True).fill('50')
        manual_time = p.evaluate("new Date(Date.parse('2026-10-17T00:00:00Z') - new Date('2026-10-17T00:00:00Z').getTimezoneOffset()*60000).toISOString().slice(0,16)")
        p.get_by_label('实际观测时间', exact=True).fill(manual_time)
        p.get_by_label('已核对作品、实际账号及后台数值', exact=True).check()
        p.get_by_role('button', name='保存人工观测', exact=True).click()
        expect(p.locator('.snapshot-card')).to_have_count(3)
        expect(p.locator('.snapshot-card').first).to_contain_text('发布后 1 天 12 小时')
        sent = self.calls('POST', '/publish-records/51/memory/metrics')[-1][2]
        self.assertEqual(sent['metrics'], {'impressions': 50})
        p.get_by_role('button', name='AI 分析并积累经验', exact=True).click()
        self.assertEqual(len(self.calls('POST', '/publish-records/51/memory/analyze')), 0)
        p.get_by_role('dialog').get_by_role('button', name='确认分析', exact=True).click()
        expect(p.get_by_text('最新经验 v1', exact=True)).to_be_visible()
        self.assertEqual(set(self.calls('POST', '/publish-records/51/memory/analyze')[-1][2]['metrics_ids']), {1,2,3})
        p.get_by_role('button', name='查看本版分析证据', exact=True).click()
        expect(p.get_by_text('证据 #3', exact=False)).to_be_visible()
        p.get_by_label('本轮补充说明').fill('下一轮比较相同发布时长的观测')
        p.get_by_role('button', name='AI 分析并积累经验', exact=True).click()
        p.get_by_role('dialog').get_by_role('button', name='确认分析', exact=True).click()
        expect(p.get_by_text('最新经验 v2', exact=True)).to_be_visible()
        p.get_by_label('运营经验版本', exact=True).select_option('1')
        p.get_by_role('button', name='停用此经验', exact=True).click()
        expect(p.get_by_text('已停用', exact=True)).to_be_visible()
        p.reload(); expect(p.locator('.snapshot-card')).to_have_count(3)
        p.set_viewport_size({'width': 390, 'height': 844})
        expect(p.locator('.app-body')).to_have_css('margin-left', '0px')
        self.assertLessEqual(p.evaluate('document.documentElement.scrollWidth'), 390)

    def test_platform_switch_isolates_configuration_and_blocks_unimplemented_workflow(self):
        p = self.page; self.login('/generate')
        expect(p.get_by_role('button', name='创建任务', exact=True)).to_be_enabled()
        p.get_by_label('选择产品', exact=True).select_option('11')
        p.get_by_label('本次运营需求').fill('小红书的运营需求')
        expect(p.get_by_label('目标平台', exact=True).locator('option')).to_have_count(2)
        p.get_by_label('目标平台', exact=True).select_option('other')
        expect(p.get_by_role('button', name='创建任务', exact=True)).to_be_disabled()
        expect(p.get_by_label('本次运营需求')).to_have_value('')
        expect(p.get_by_text('其他平台 已预留平台入口', exact=False)).to_be_visible()
        self.assertEqual(p.get_by_role('button', name='选择参考案例', exact=True).count(), 0)
        p.get_by_label('本次运营需求').fill('其他平台 的运营需求')
        p.get_by_role('button', name='Prompt 模板', exact=True).click()
        dialog = p.get_by_role('dialog')
        dialog.get_by_label('模板名称').fill('其他平台 专用模板')
        dialog.get_by_label('Prompt 内容').fill('其他平台 平台的风格')
        dialog.get_by_role('button', name='保存并应用').click()
        expect(dialog).not_to_be_visible()
        self.assertEqual(self.calls('POST', '/prompts')[-1][2]['platform'], 'other')
        p.get_by_label('目标平台', exact=True).select_option('rednote')
        expect(p.get_by_label('本次运营需求')).to_have_value('小红书的运营需求')
        expect(p.get_by_label('生成 Prompt', exact=True)).to_have_value('')
        self.assertEqual(p.get_by_label('生成 Prompt', exact=True).locator('option').count(), 1)
        p.get_by_label('目标平台', exact=True).select_option('other')
        expect(p.get_by_label('本次运营需求')).to_have_value('其他平台 的运营需求')
        p.reload()
        expect(p.get_by_label('目标平台', exact=True)).to_have_value('other')
        expect(p.get_by_role('button', name='创建任务', exact=True)).to_be_disabled()
        self.assertEqual(len(self.calls('POST', '/tasks')), 0)
        self.assertEqual(len(self.calls('POST', '/tasks/31/workflow/start')), 0)

    def test_custom_model_management_and_selection_are_persisted_without_auto_analysis(self):
        p = self.page; self.login('/cases')
        p.get_by_role('button', name='＋ 添加或管理分析模型', exact=True).click()
        dialog = p.get_by_role('dialog')
        dialog.get_by_label('显示名称').fill('通义分析模型')
        dialog.get_by_label('模型供应商', exact=True).fill('自选供应商')
        dialog.get_by_label('API URL', exact=True).fill('https://api.vendor.example/v1')
        dialog.get_by_label('API Key', exact=True).fill('browser-test-only-key')
        dialog.get_by_label('模型 ID', exact=True).fill('configured-qwen-chat')
        dialog.get_by_role('button', name='保存模型', exact=True).click()
        expect(dialog.get_by_role('button', name='编辑模型', exact=True)).to_be_visible()
        expect(dialog.get_by_label('API Key', exact=True)).to_have_value('')
        self.assertEqual(len(self.calls('POST', '/analysis-models/91/test')), 0)
        self.assertNotIn('browser-test-only-key', p.evaluate('JSON.stringify({...localStorage,...sessionStorage})'))
        dialog.get_by_role('button', name='测试连接', exact=True).click()
        expect(p.get_by_role('status').filter(has_text='模型连接成功')).to_be_visible()
        self.assertEqual(len(self.calls('POST', '/analysis-models/91/test')), 1)
        self.assertEqual(len(self.calls('POST', '/rednote/cases/71/analyze')), 0)
        dialog.get_by_role('button', name='完成', exact=True).click()
        p.get_by_label('分析模型', exact=True).select_option('user:91')
        p.reload()
        expect(p.get_by_label('分析模型', exact=True)).to_have_value('user:91')
        p.get_by_role('button', name='重新分析 · 新版本', exact=True).click()
        expect(p.get_by_label('分析版本', exact=True)).to_have_value('82')
        self.assertEqual(self.calls('POST', '/rednote/cases/71/analyze')[-1][2]['model_id'], 'user:91')
        p.get_by_role('button', name='＋ 添加或管理分析模型', exact=True).click()
        dialog = p.get_by_role('dialog')
        dialog.get_by_role('button', name='编辑模型', exact=True).click()
        expect(dialog.get_by_label('API URL', exact=True)).to_have_value('https://api.vendor.example/v1')
        expect(dialog.get_by_label('API Key', exact=True)).to_have_value('')
        dialog.get_by_label('启用此模型').uncheck()
        dialog.get_by_role('button', name='保存模型', exact=True).click()
        expect(dialog.get_by_text('自选供应商 · configured-qwen-chat · 已停用', exact=True)).to_be_visible()
        self.assertIsNone(self.calls('PUT', '/analysis-models/91')[-1][2]['api_key'])
        dialog.get_by_role('button', name='完成', exact=True).click()
        expect(p.get_by_label('分析模型', exact=True)).to_have_value('test-model')
        p.get_by_role('button', name='＋ 添加或管理分析模型', exact=True).click()
        dialog = p.get_by_role('dialog')
        dialog.locator('.model-row').filter(has_text='configured-qwen-chat').get_by_role('button', name='删除', exact=True).click()
        dialog.get_by_role('button', name='取消', exact=True).click()
        self.assertEqual(len(self.calls('DELETE', '/analysis-models/91')), 0)
        dialog.locator('.model-row').filter(has_text='configured-qwen-chat').get_by_role('button', name='删除', exact=True).click()
        dialog.get_by_role('button', name='确认删除模型', exact=True).click()
        expect(dialog.get_by_role('button', name='编辑模型', exact=True)).to_have_count(0)
        dialog.get_by_role('button', name='完成', exact=True).click()

    def test_system_model_deletion_is_confirmed_and_can_be_restored(self):
        p = self.page; self.login('/settings')
        row = p.locator('.model-row').filter(has_text='Default analysis')
        row.get_by_role('button', name='删除', exact=True).click()
        p.get_by_role('button', name='取消', exact=True).click()
        self.assertEqual(self.calls('DELETE', '/analysis-models/system/test-model'), [])
        expect(row).to_be_visible()
        row.get_by_role('button', name='删除', exact=True).click()
        p.get_by_role('button', name='确认删除模型', exact=True).click()
        expect(p.locator('.model-row')).to_have_count(0)
        expect(p.get_by_text('暂无模型，请添加模型连接或恢复内置模型后再生成内容。', exact=True)).to_be_visible()
        p.reload()
        expect(p.locator('.model-row')).to_have_count(0)
        p.get_by_role('button', name='恢复内置模型', exact=True).click()
        expect(p.locator('.model-row').filter(has_text='Default analysis')).to_be_visible()
        self.assertEqual(len(self.calls('DELETE', '/analysis-models/system/test-model')), 1)
        self.assertEqual(len(self.calls('POST', '/analysis-models/system/restore')), 1)

    def test_embedding_and_image_connections_can_be_created_edited_deleted_and_selected(self):
        p = self.page; self.login('/settings')
        p.get_by_role('button', name='Embedding', exact=True).click()
        p.get_by_label('显示名称', exact=True).fill('向量连接')
        p.get_by_label('模型供应商', exact=True).fill('自选向量供应商')
        p.get_by_label('API URL', exact=True).fill('https://api.vendor.example/v1')
        p.get_by_label('API Key', exact=True).fill('resource-browser-test-key')
        p.get_by_label('模型 ID', exact=True).fill('custom-embedding')
        p.get_by_label('向量维度', exact=True).fill('768')
        p.get_by_role('button', name='保存模型', exact=True).click()
        expect(p.get_by_role('button', name='编辑模型', exact=True)).to_be_visible()
        expect(p.get_by_label('API Key', exact=True)).to_have_value('')
        self.assertEqual(self.calls('POST', '/resource-models')[-1][2]['kind'], 'embedding')
        p.get_by_role('button', name='编辑模型', exact=True).click()
        p.get_by_label('显示名称', exact=True).fill('更新后的向量连接')
        p.get_by_role('button', name='保存模型', exact=True).click()
        expect(p.locator('.model-row')).to_contain_text('更新后的向量连接')
        self.assertIsNone(self.calls('PUT', '/resource-models/101')[-1][2]['api_key'])
        p.get_by_role('button', name='删除', exact=True).click()
        p.get_by_role('button', name='取消', exact=True).click()
        self.assertEqual(self.calls('DELETE', '/resource-models/101'), [])
        p.get_by_role('button', name='删除', exact=True).click()
        p.get_by_role('button', name='确认删除模型', exact=True).click()
        expect(p.locator('.model-row')).to_have_count(0)
        p.get_by_role('button', name='生图模型', exact=True).click()
        p.get_by_label('显示名称', exact=True).fill('配图连接')
        p.get_by_label('模型供应商', exact=True).fill('自选图片供应商')
        p.get_by_label('API URL', exact=True).fill('https://images.vendor.example/v1')
        p.get_by_label('API Key', exact=True).fill('resource-browser-image-key')
        p.get_by_label('模型 ID', exact=True).fill('custom-image')
        p.get_by_label('支持的图片尺寸', exact=True).fill('1024*1024, 1024*1536')
        p.get_by_role('button', name='保存模型', exact=True).click()
        expect(p.get_by_role('button', name='编辑模型', exact=True)).to_be_visible()
        self.assertNotIn('resource-browser-image-key', p.evaluate('JSON.stringify({...localStorage,...sessionStorage})'))
        p.get_by_role('link', name='内容创作', exact=True).click()
        p.get_by_label('生成内容', exact=True).select_option('images')
        expect(p.get_by_label('生图模型', exact=True).locator('option')).to_contain_text(['使用账号默认生图模型', '配图连接'])
        expect(p.get_by_label('图片比例', exact=True)).to_have_value('1024*1024')
        p.get_by_label('生图模型', exact=True).select_option('101')
        self.fixture.resource_models = []
        p.reload()
        expect(p.get_by_label('生图模型', exact=True)).to_have_value('')

    def test_custom_model_is_available_for_content_generation_and_key_does_not_reappear(self):
        p = self.page; self.login('/settings')
        p.get_by_label('显示名称', exact=True).fill('独立文案模型')
        p.get_by_label('模型供应商', exact=True).fill('自定义供应商')
        p.get_by_label('API URL', exact=True).fill('https://api.vendor.example/v1')
        p.get_by_label('API Key', exact=True).fill('test-generation-key')
        p.get_by_label('模型 ID', exact=True).fill('vendor/chat-model')
        p.get_by_role('button', name='保存模型', exact=True).click()
        expect(p.get_by_role('button', name='编辑模型', exact=True)).to_be_visible()
        self.assertTrue(self.calls('POST', '/analysis-models')[-1][2]['is_default'])
        p.reload()
        p.get_by_role('button', name='编辑模型', exact=True).click()
        expect(p.get_by_label('API Key', exact=True)).to_have_value('')
        p.screenshot(path=str(ARTIFACTS / 'model-connections.png'), full_page=True, animations='disabled')
        p.get_by_role('link', name='内容创作', exact=True).click()
        p.get_by_label('选择产品', exact=True).select_option('11')
        p.get_by_label('本次运营需求').fill('使用所选模型生成文案')
        p.get_by_text('风格与生成偏好', exact=True).click()
        p.get_by_label('文案生成模型', exact=True).select_option('user:91')
        p.get_by_role('button', name='创建任务', exact=True).click()
        expect(p.get_by_role('button', name='启动文案生成', exact=True)).to_be_enabled()
        p.get_by_role('button', name='启动文案生成', exact=True).click()
        expect(p.get_by_role('link', name='前往审核 →', exact=True)).to_be_visible()
        self.assertEqual(self.calls('POST', '/tasks/31/workflow/start')[-1][2]['model_id'], 'user:91')
        self.assertNotIn('test-generation-key', p.evaluate('JSON.stringify({...localStorage,...sessionStorage})'))

    def test_product_crud_and_delete_confirmation_keep_untrusted_text_as_text(self):
        self.fixture.products = []; self.fixture.docs = []
        p = self.page; self.login()
        p.get_by_role("button", name="＋ 新建产品", exact=True).click()
        dialog = p.get_by_role("dialog")
        title = "<img src=x onerror=window.__xss=1>"
        dialog.get_by_label("产品名称").fill(title)
        dialog.get_by_label("产品简介").fill("测试简介")
        dialog.get_by_role("button", name="保存产品", exact=True).click()
        expect(p.get_by_role("heading", name=title, exact=True)).to_be_visible()
        self.assertIsNone(p.evaluate("window.__xss"))
        p.get_by_role("button", name="编辑", exact=True).click()
        dialog = p.get_by_role("dialog"); dialog.get_by_label("产品名称").fill("编辑后的产品"); dialog.get_by_role("button", name="保存产品", exact=True).click()
        expect(p.get_by_role("heading", name="编辑后的产品", exact=True)).to_be_visible()
        p.get_by_role("button", name="删除产品", exact=True).click()
        p.get_by_role("dialog").get_by_role("button", name="取消", exact=True).click()
        self.assertEqual(len(self.calls("DELETE", "/products/11")), 0)
        p.get_by_role("button", name="删除产品", exact=True).click()
        p.get_by_role("dialog").get_by_role("button", name="确认删除", exact=True).click()
        expect(p.get_by_role("heading", name="暂无产品", exact=True)).to_be_visible()
        self.assertEqual(len(self.calls("DELETE", "/products/11")), 1)

    def test_image_ratios_are_exact_and_sent_to_workflow_and_review(self):
        p = self.page; self.login('/generate')
        p.get_by_label('选择产品', exact=True).select_option('11')
        p.get_by_label('本次运营需求').fill('生成产品配图')
        p.get_by_label('生成内容', exact=True).select_option('images')
        select = p.get_by_label('图片比例', exact=True)
        expect(select.locator('option')).to_have_text(['1:1', '3:4', '4:3', '16:9', '9:16'])
        self.assertEqual(select.get_attribute('translate'), 'no')
        for ratio, size in [('1:1', '2048*2048'), ('3:4', '1536*2048'), ('4:3', '2048*1536'),
                            ('16:9', '2048*1152'), ('9:16', '1152*2048')]:
            select.select_option(label=ratio)
            expect(select).to_have_value(size)
            expect(p.get_by_text(size.replace('*', ' × ') + ' px', exact=True)).to_be_visible()
        p.get_by_role('button', name='创建任务', exact=True).click()
        p.get_by_role('button', name='启动图片生成', exact=True).click()
        expect(p.get_by_role('link', name='前往审核 →')).to_be_visible()
        self.assertEqual(self.calls('POST', '/tasks/31/workflow/start')[-1][2]['image_size'], '1152*2048')
        p.get_by_role('link', name='前往审核 →').click()
        p.get_by_text('生成 / 重新生成配图', exact=True).click()
        select = p.get_by_label('图片比例', exact=True)
        expect(select.locator('option')).to_have_text(['1:1', '3:4', '4:3', '16:9', '9:16'])
        select.select_option(label='16:9')
        p.get_by_role('button', name='生成配图', exact=True).click()
        with p.expect_response(API + '/contents/42/images/generate'):
            p.get_by_role('dialog', name='确认生成配图').get_by_role('button', name='确认生图').click()
        self.assertEqual(self.calls('POST', '/contents/42/images/generate')[-1][2]['size'], '2048*1152')

    def test_complete_workflow_and_publication_confirmation(self):
        p = self.page; self.login()
        expect(p.get_by_role("heading", name="产品管理", exact=True)).to_be_visible()
        p.get_by_role("link", name="内容创作", exact=True).click()
        p.get_by_label("选择产品", exact=True).select_option("11")
        p.get_by_label("本次运营需求").fill("通勤场景介绍，保持产品资料事实")
        p.get_by_role("button", name="Prompt 模板", exact=True).click()
        dialog = p.get_by_role("dialog")
        dialog.get_by_label("模板名称").fill("自然表达")
        dialog.get_by_label("Prompt 内容").fill("先写通勤场景，不编造体验。")
        dialog.get_by_role("button", name="保存并应用").click()
        expect(dialog).not_to_be_visible()
        self.choose_reference()
        p.get_by_role("button", name="创建任务", exact=True).click()
        expect(p.get_by_role("button", name="启动文案生成")).to_be_enabled()
        p.get_by_role("button", name="启动文案生成").dblclick()
        expect(p.get_by_role("link", name="前往审核 →")).to_be_visible()
        self.assertEqual(len(self.calls("POST", "/tasks/31/workflow/start")), 1)
        sent = self.calls("POST", "/tasks/31/workflow/start")[0][2]
        self.assertEqual(sent["prompt_id"], 21); self.assertEqual(sent["analysis_ids"], [81])
        p.evaluate("window.scrollTo(0,0)")
        p.screenshot(path=str(ARTIFACTS / "generate.png"), full_page=True)
        p.get_by_role("link", name="前往审核 →").click()
        expect(p.get_by_label("正文", exact=True)).to_have_value(self.fixture.contents[0]["body"])
        editor = p.locator('#content-editor').bounding_box()
        checks = p.locator('#review-checks').bounding_box()
        self.assertGreater(editor['width'], 850)
        self.assertGreaterEqual(checks['y'], editor['y'] + editor['height'])
        p.get_by_label("正文", exact=True).fill("人工修改后的演示文案，资料事实保持一致。")
        expect(p.get_by_role("button", name="通过审核", exact=True)).to_be_disabled()
        p.get_by_role("button", name="保存并提交审核", exact=True).click()
        expect(p.locator(".version-select")).to_have_value("42")
        expect(p.get_by_role("button", name="通过审核", exact=True)).to_be_enabled()
        p.get_by_text("生成 / 重新生成配图", exact=True).click()
        p.get_by_role("button", name="生成配图", exact=True).click()
        self.assertEqual(len(self.calls("POST", "/contents/42/images/generate")), 0)
        p.get_by_role("dialog").get_by_role("button", name="确认生图").click()
        expect(p.locator(".version-select")).to_have_value("43")
        expect(p.get_by_alt_text("配图 1", exact=True)).to_be_visible()
        p.get_by_role("button", name="通过审核", exact=True).click()
        dialog = p.get_by_role("dialog"); dialog.get_by_role("checkbox").check(); dialog.get_by_role("button", name="确认通过").click()
        expect(p.get_by_label("发布账号", exact=True)).to_be_visible()
        p.get_by_label("发布账号", exact=True).select_option("61")
        p.get_by_role("button", name="确认内容并发布").click()
        p.get_by_role("dialog").get_by_role("button", name="取消", exact=True).click()
        self.assertEqual(len(self.calls("POST", "/publish-records")), 0)
        p.get_by_role("button", name="确认内容并发布").click()
        dialog = p.get_by_role("dialog"); dialog.get_by_role("checkbox").check(); dialog.get_by_role("button", name="确认发布", exact=True).click()
        expect(p.get_by_text("结果不明 · 请核实", exact=True)).to_be_visible()
        self.assertEqual(len(self.calls("POST", "/publish-records/51/execute")), 1)
        self.assertEqual(self.calls("POST", "/publish-records")[0][2]["content_id"], 43)
        self.assertEqual(self.calls("POST", "/publish-records")[0][2]["visibility"], "private")
        p.reload(); expect(p.get_by_text("结果不明 · 请核实", exact=True)).to_be_visible()
        self.assertEqual(len(self.calls("POST", "/publish-records/51/execute")), 1)
        p.get_by_role("button", name="核实并登记实际结果").click()
        dialog = p.get_by_role("dialog"); dialog.get_by_label("完整作品链接").fill("https://www.xiaohongshu.com/explore/" + "1" * 24); dialog.get_by_role("checkbox").check(); dialog.get_by_role("button", name="保存核实结果").click()
        expect(p.get_by_role('link', name='数据反馈与运营经验 →', exact=True)).to_be_visible()
        p.evaluate('window.scrollTo(0,0)'); p.screenshot(path=str(ARTIFACTS / 'review.png'), full_page=True)
        p.get_by_role('link', name='数据反馈与运营经验 →', exact=True).click()
        expect(p.get_by_role("heading", name="数据反馈与运营经验", exact=True)).to_be_visible()
        p.get_by_role("button", name="采集当前真实指标").click()
        expect(p.locator('.metric').filter(has_text='点赞').locator('strong')).to_have_text('0')
        p.get_by_role("button", name="AI 分析并积累经验").click()
        self.assertEqual(len(self.calls("POST", "/publish-records/51/memory/analyze")), 0)
        p.get_by_role("dialog").get_by_role("button", name="确认分析", exact=True).click()
        expect(p.get_by_text("最新经验 v1", exact=True)).to_be_visible()
        p.evaluate("window.scrollTo(0,0)")
        bounds = p.evaluate("({ body: document.querySelector('.app-body').getBoundingClientRect().left, sidebar: document.querySelector('.sidebar').getBoundingClientRect().right, root: document.querySelector('.app').className, scroll: window.scrollX })")
        self.assertGreaterEqual(bounds['body'], bounds['sidebar'] - 1, bounds)
        layout = p.evaluate("Object.fromEntries(['.app-body','.main','.page-heading','.memory-panel'].map(s => { const e=document.querySelector(s),r=e.getBoundingClientRect(),c=getComputedStyle(e); return [s,{left:r.left,width:r.width,scrollLeft:e.scrollLeft,scrollWidth:e.scrollWidth,clientWidth:e.clientWidth,overflow:c.overflow,margin:c.margin,position:c.position}]}))")
        self.assertGreaterEqual(layout['.page-heading']['left'], bounds['sidebar'] - 1, layout)
        p.screenshot(path=str(ARTIFACTS / "feedback.png"), full_page=True, animations='disabled')
        self.assertEqual(self.fixture.prompts[0]["prompt_content"], "先写通勤场景，不编造体验。")
        self.assertEqual(len(self.calls("POST", "/publish-records/51/execute")), 1)

    def test_unchanged_saved_version_can_submit_without_fabricating_a_new_version(self):
        self.fixture = ContractAPI(ready=True)
        self.fixture.task['status'] = 'running'
        self.fixture.check_status = 'risk_detected'
        self.fixture.workflow_stage_override = 'waiting_rule_changes'
        self.page.unroute(API + '/**'); self.page.route(API + '/**', self.fixture.handle)
        p = self.page; self.login('/review?task_id=31')
        expect(p.get_by_role('button', name='保存修改 · 新版本', exact=True)).to_be_disabled()
        p.get_by_role('button', name='提交已保存版本审核', exact=True).click()
        expect(p.get_by_role('button', name='已提交审核', exact=True)).to_be_disabled()
        expect(p.get_by_role('button', name='通过审核', exact=True)).to_be_enabled()
        self.assertEqual(self.calls('POST', '/tasks/31/workflow/resume')[-1][2]['action'], 'submit_review')
        self.assertEqual(len(self.calls('PATCH', '/contents/41')), 0)
        self.assertEqual(len(self.fixture.contents), 1)

    def test_saved_changes_can_retry_submission_without_saving_another_version(self):
        self.fixture = ContractAPI(ready=True)
        self.page.unroute(API + '/**'); self.page.route(API + '/**', self.fixture.handle)
        p = self.page; self.login('/review?task_id=31')
        p.get_by_label('正文', exact=True).fill('已保存的新文案')
        p.get_by_role('button', name='保存修改 · 新版本', exact=True).click()
        expect(p.get_by_label('内容版本', exact=True)).to_have_value('42')
        expect(p.get_by_role('button', name='保存修改 · 新版本', exact=True)).to_be_disabled()
        def fail(route): route.abort('failed')
        p.route(API + '/tasks/31/workflow/resume', fail)
        p.get_by_role('button', name='提交已保存版本审核', exact=True).click()
        expect(p.get_by_role('button', name='提交已保存版本审核', exact=True)).to_be_enabled()
        p.unroute(API + '/tasks/31/workflow/resume', fail)
        p.get_by_role('button', name='提交已保存版本审核', exact=True).click()
        expect(p.get_by_role('button', name='通过审核', exact=True)).to_be_enabled()
        self.assertEqual(self.calls('POST', '/tasks/31/workflow/resume')[-1][2]['action'], 'submit_changes')
        self.assertEqual(len(self.calls('PATCH', '/contents/41')), 1)
        self.assertEqual(len(self.fixture.contents), 2)

    def test_case_prompt_and_rule_creation_edit_and_deletion_are_available(self):
        p = self.page; self.login('/cases')
        p.get_by_role('button', name='编辑案例', exact=True).click()
        dialog = p.get_by_role('dialog')
        dialog.get_by_label('案例标题').fill('人工修正的案例')
        dialog.get_by_role('button', name='保存案例', exact=True).click()
        expect(p.get_by_text('人工修正的案例', exact=True)).to_be_visible()
        p.get_by_role('button', name='删除案例', exact=True).click()
        expect(p.get_by_text('案例库暂无匹配内容，可以先采集帖子链接。', exact=True)).to_be_visible()
        p.get_by_role('button', name='＋ 手动添加案例', exact=True).click()
        dialog = p.get_by_role('dialog')
        dialog.get_by_label('案例标题').fill('新添加的案例')
        dialog.get_by_label('案例链接').fill('https://www.xiaohongshu.com/explore/' + '3' * 24)
        dialog.get_by_label('案例正文').fill('手动输入的案例正文')
        dialog.get_by_role('button', name='保存案例', exact=True).click()
        expect(p.get_by_text('新添加的案例', exact=True)).to_be_visible()
        p.get_by_role('link', name='设置', exact=True).click()
        p.get_by_role('link', name='Prompt 模板', exact=True).click()
        p.get_by_label('模板名称').fill('可维护模板')
        p.get_by_label('Prompt 内容').fill('自定义表达')
        p.get_by_role('button', name='保存模板', exact=True).click()
        expect(p.get_by_role('button', name='删除模板', exact=True)).to_be_visible()
        p.get_by_label('Prompt 内容').fill('修改后的表达')
        p.get_by_role('button', name='保存模板', exact=True).click()
        expect(p.get_by_role('button', name='保存模板', exact=True)).to_be_enabled()
        p.get_by_role('button', name='删除模板', exact=True).click()
        expect(p.get_by_role('button', name='删除模板', exact=True)).to_have_count(0)
        p.get_by_role('link', name='平台规则', exact=True).click()
        p.get_by_label('规则说明').fill('仅供参考的建议')
        p.get_by_role('button', name='保存规则', exact=True).click()
        expect(p.get_by_label('已有规则').locator('option')).to_have_count(2)
        p.get_by_label('已有规则').select_option('101')
        p.get_by_label('规则说明').fill('调整后的建议')
        p.get_by_role('button', name='保存规则', exact=True).click()
        expect(p.get_by_role('button', name='保存规则', exact=True)).to_be_enabled()
        p.get_by_role('button', name='删除规则', exact=True).click()
        expect(p.get_by_label('已有规则').locator('option')).to_have_count(1)
        self.assertEqual(len(self.calls('DELETE', '/rednote/cases/71')), 1)
        self.assertEqual(len(self.calls('DELETE', '/prompts/21')), 1)
        self.assertEqual(len(self.calls('DELETE', '/rules/custom/101')), 1)

    def test_images_only_can_be_approved_downloaded_and_manually_published_with_external_text(self):
        p = self.page; self.login('/generate')
        p.get_by_label('选择产品', exact=True).select_option('11')
        p.get_by_label('本次运营需求').fill('仅生成通勤主题图片')
        p.get_by_label('生成内容', exact=True).select_option('images')
        p.get_by_role('button', name='创建任务', exact=True).click()
        p.get_by_role('button', name='启动图片生成', exact=True).click()
        expect(p.get_by_role('link', name='前往审核 →')).to_be_visible()
        self.assertEqual(self.calls('POST', '/tasks/31/workflow/start')[-1][2]['output_mode'], 'images')
        p.get_by_role('link', name='前往审核 →').click()
        expect(p.get_by_label('正文', exact=True)).to_have_value('')
        expect(p.get_by_alt_text('配图 1', exact=True)).to_be_visible()
        with p.expect_download() as download:
            p.get_by_role('link', name='下载图片', exact=True).click()
        self.assertEqual(download.value.suggested_filename, 'content-42-image-1.png')
        p.get_by_role('button', name='通过审核', exact=True).click()
        dialog = p.get_by_role('dialog'); dialog.get_by_role('checkbox').check(); dialog.get_by_role('button', name='确认通过', exact=True).click()
        expect(p.get_by_label('发布账号', exact=True)).to_be_visible()
        p.get_by_label('发布账号', exact=True).select_option('61')
        expect(p.get_by_role('button', name='确认内容并发布', exact=True)).to_be_disabled()
        p.get_by_role('button', name='自行发布并登记结果', exact=True).click()
        dialog = p.get_by_role('dialog'); dialog.get_by_role('checkbox').check(); dialog.get_by_role('button', name='创建结果核实记录', exact=True).click()
        dialog = p.get_by_role('dialog')
        dialog.get_by_label('完整作品链接').fill('https://www.xiaohongshu.com/explore/' + '1' * 24)
        dialog.get_by_text('平台补充或修改的素材（选填）', exact=True).click()
        dialog.get_by_label('最终发布正文').fill('在平台手写的文案')
        dialog.get_by_label('平台补充素材说明').fill('图片来自工作流，文案在平台补充')
        dialog.get_by_role('checkbox').check()
        dialog.get_by_role('button', name='保存核实结果', exact=True).click()
        expect(p.get_by_role('link', name='数据反馈与运营经验 →', exact=True)).to_be_visible()
        self.assertEqual(self.calls('POST', '/publish-records')[-1][2]['publisher_backend'], 'manual')
        self.assertEqual(len(self.calls('POST', '/publish-records/51/execute')), 0)
        self.assertEqual(self.fixture.records[0]['publication_details']['final_content']['body'], '在平台手写的文案')

    def test_failed_save_preserves_draft_after_refresh_and_logout_clears_session(self):
        self.fixture = ContractAPI(ready=True)
        self.page.unroute(API + "/**"); self.page.route(API + "/**", self.fixture.handle)
        p = self.page; self.login("/review?task_id=31")
        p.get_by_label("正文", exact=True).fill("未提交的本机草稿")
        self.fixture.save_failure = True
        p.get_by_role("button", name="保存修改 · 新版本", exact=True).click()
        expect(p.get_by_role("button", name="保存修改 · 新版本", exact=True)).to_be_enabled()
        expect(p.get_by_label("正文", exact=True)).to_have_value("未提交的本机草稿")
        p.reload()
        expect(p.get_by_label("正文", exact=True)).to_have_value("未提交的本机草稿")
        expect(p.get_by_text("已恢复此版本的本机未提交草稿。点击保存后才会写入后端。", exact=True)).to_be_visible()
        p.get_by_role("button", name="退出登录", exact=True).click()
        expect(p.get_by_role("heading", name="登录工作台")).to_be_visible()
        self.assertIsNone(p.evaluate("sessionStorage.getItem('operations.session.v1')"))
        self.assertEqual(p.evaluate("Object.keys(localStorage).filter(k=>k.startsWith('operations.user.101.')).length"), 0)

    def test_upload_and_index_are_distinct_and_session_expiry_redirects(self):
        p = self.page; self.login('/products/11')
        toggle = p.get_by_role('button', name='切换侧栏', exact=True)
        toggle.click(); expect(toggle).to_have_attribute('aria-expanded', 'false')
        toggle.click(); expect(toggle).to_have_attribute('aria-expanded', 'true')
        p.get_by_label("选择产品资料").set_input_files({"name": "补充资料.txt", "mimeType": "text/plain", "buffer": b"extra facts"})
        p.get_by_role("button", name="上传所选文件 / 重试失败项").click()
        expect(p.get_by_text("已上传 · 待入库", exact=True).last).to_be_visible()
        self.assertEqual(self.fixture.docs[-1]["status"], "uploaded")
        p.get_by_role("button", name="建立知识库 / 重试", exact=True).click()
        expect(p.get_by_text("已入库", exact=True).last).to_be_visible()
        self.assertEqual(self.fixture.docs[-1]["status"], "indexed")
        p.screenshot(path=str(ARTIFACTS / "products.png"), full_page=True)
        p.set_viewport_size({"width": 390, "height": 844})
        expect(toggle).to_have_attribute('aria-expanded', 'false')
        toggle.click(); expect(toggle).to_have_attribute('aria-expanded', 'true')
        p.get_by_role('button', name='关闭侧栏', exact=True).click(position={"x": 380, "y": 50})
        expect(toggle).to_have_attribute('aria-expanded', 'false')
        p.screenshot(path=str(ARTIFACTS / "mobile.png"), full_page=True)
        self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth"), 390)
        self.fixture.expired = True
        p.get_by_role("button", name="刷新", exact=True).click()
        expect(p.get_by_role("heading", name="登录工作台")).to_be_visible()


if __name__ == "__main__":
    unittest.main()
