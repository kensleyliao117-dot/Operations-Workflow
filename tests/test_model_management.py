"""模型配置隔离、实际适配器选择与平台能力边界。"""
import json
from unittest.mock import patch
import unittest

from sqlalchemy.orm import Session

from models.rednote import RednoteCase
from services import analysis_models
from services.exceptions import InvalidInputError, NotFoundError
import test_business_api as api_tests
from test_collection_api import VALID_ANALYSIS


class ModelManagementTest(unittest.TestCase):
    setUp = api_tests.BusinessAPITest.setUp
    tearDown = api_tests.BusinessAPITest.tearDown
    post = api_tests.BusinessAPITest.post
    register_second_user = api_tests.BusinessAPITest.register_second_user

    def test_platform_catalog_requires_auth_and_reports_only_implemented_capabilities(self):
        result = self.client.get('/platforms')
        self.assertEqual(result.status_code, 200)
        values = {p['id']: p for p in result.json()}
        self.assertEqual(set(values), {'rednote', 'other'})
        self.assertTrue(values['rednote']['workflow'])
        self.assertTrue(values['rednote']['generation'])
        self.assertEqual(values['other']['name'], '其他平台')
        for capability in ('workflow', 'publishing', 'generation', 'collection'):
            self.assertFalse(values['other'][capability])
        self.client.headers.pop('Authorization')
        self.assertEqual(self.client.get('/platforms').status_code, 401)
        self.assertEqual(self.client.get('/analysis-models').status_code, 401)

    def test_crud_owner_isolation_and_no_credentials_in_response(self):
        data = {'name': 'Personal model', 'provider': 'qwen', 'model': 'configured-chat-model'}
        with patch('config.DASHSCOPE_API_KEY', 'secret-model-key'):
            value = self.post('/analysis-models', data)
        self.assertTrue(value['ready'])
        self.assertEqual(value['id'], f"user:{value['config_id']}")
        self.assertEqual(value['source'], 'user')
        self.assertNotIn('secret-model-key', json.dumps(value))
        self.post('/analysis-models', data, 409)
        self.post('/analysis-models', {**data, 'user_id': 55}, 422)
        self.post('/analysis-models', {**data, 'base_url': 'http://localhost:9222'}, 422)
        for model in ('text-embedding-v3', 'name\ninvalid', 'x' * 101):
            self.post('/analysis-models', {**data, 'model': model}, 422)
        self.post('/analysis-models', {**data, 'provider': 'arbitrary'}, 422)
        response = self.client.put(f"/analysis-models/{value['config_id']}", json={**data, 'is_active': False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['ready'])
        with Session(self.engine) as db:
            db.info['user_id'] = self.user_id
            with self.assertRaises(InvalidInputError): analysis_models.select_model(db, value['id'])
        owner_headers = dict(self.client.headers)
        self.register_second_user()
        self.assertFalse(any(item['id'] == value['id'] for item in self.client.get('/analysis-models').json()))
        for method in ('put', 'delete'):
            kwargs = {'json': data} if method == 'put' else {}
            self.assertEqual(getattr(self.client, method)(f"/analysis-models/{value['config_id']}", **kwargs).status_code, 404)
        with Session(self.engine) as db:
            db.info['user_id'] = self.client.get('/auth/me').json()['id']
            with self.assertRaises(NotFoundError): analysis_models.select_model(db, value['id'])
        self.client.headers.update(owner_headers)
        self.assertEqual(self.client.delete(f"/analysis-models/{value['config_id']}").status_code, 204)

    def test_configured_model_reaches_adapter_and_edit_does_not_change_saved_analysis(self):
        data = {'name': 'Alternative', 'provider': 'deepseek', 'model': 'other-configured-chat'}
        profile = self.post('/analysis-models', data)
        case = self.post('/rednote/cases', {'name': 'Case', 'body': 'Source text', 'link': 'https://www.xiaohongshu.com/explore/' + 'a' * 24})
        with patch('integrations.llm.deepseek.generate_text', return_value=json.dumps(VALID_ANALYSIS)) as model:
            saved = self.post(f"/rednote/cases/{case['id']}/analyze", {'model_id': profile['id']})
            self.assertEqual(model.call_args.kwargs['model'], data['model'])
        self.assertEqual(saved['model'], data['model'])
        self.assertEqual(saved['model_profile'], profile['id'])
        self.client.put(f"/analysis-models/{profile['config_id']}", json={**data, 'model': 'new-chat-version'})
        self.assertEqual(self.client.get(f"/rednote/analyses/{saved['id']}").json()['model'], data['model'])
        self.client.delete(f"/analysis-models/{profile['config_id']}")
        self.assertEqual(self.client.get(f"/rednote/analyses/{saved['id']}").json()['model'], data['model'])

    def test_system_models_can_be_removed_per_user_and_restored_without_silent_reuse(self):
        systems = [row for row in self.client.get('/analysis-models').json() if row['source'] == 'system']
        self.assertTrue(systems)
        owner_headers = dict(self.client.headers)
        for row in systems:
            path = '/analysis-models/system/' + row['id']
            self.assertEqual(self.client.delete(path).status_code, 204)
            self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.get('/analysis-models').json(), [])
        with Session(self.engine) as db:
            db.info['user_id'] = self.user_id
            for model_id in (None, systems[0]['id']):
                with self.assertRaises(InvalidInputError):
                    analysis_models.select_model(db, model_id)
        self.register_second_user()
        self.assertEqual({row['id'] for row in self.client.get('/analysis-models').json()},
                         {row['id'] for row in systems})
        self.client.headers.update(owner_headers)
        self.assertEqual(self.client.post('/analysis-models/system/restore').status_code, 204)
        self.assertEqual({row['id'] for row in self.client.get('/analysis-models').json()},
                         {row['id'] for row in systems})
        self.assertEqual(self.client.delete('/analysis-models/system/not-configured').status_code, 422)
        self.client.headers.pop('Authorization')
        self.assertEqual(self.client.delete('/analysis-models/system/' + systems[0]['id']).status_code, 401)
        self.assertEqual(self.client.post('/analysis-models/system/restore').status_code, 401)

    def test_removing_system_model_does_not_change_custom_default_or_analysis_history(self):
        system = next(row for row in self.client.get('/analysis-models').json() if row['source'] == 'system')
        case = self.post('/rednote/cases', {'name': 'Source', 'body': 'Facts',
            'link': 'https://www.xiaohongshu.com/explore/' + 'b' * 24})
        with patch('integrations.llm.analysis_models.generate_text', return_value=json.dumps(VALID_ANALYSIS)):
            saved = self.post(f"/rednote/cases/{case['id']}/analyze", {'model_id': system['id']})
        custom = self.post('/analysis-models', {'name': 'Default', 'provider': 'deepseek',
            'model': 'another-chat', 'is_default': True})
        self.assertEqual(self.client.delete('/analysis-models/system/' + system['id']).status_code, 204)
        self.assertEqual(self.client.get(f"/rednote/analyses/{saved['id']}").json()['model'], saved['model'])
        with Session(self.engine) as db:
            db.info['user_id'] = self.user_id
            self.assertEqual(analysis_models.select_model(db, None).id, custom['id'])

