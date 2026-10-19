"""素材独立生成、提示性检查与账号内资源维护。"""
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from integrations.llm.deepseek import DeepSeekTimeoutError
from integrations.llm.image import ImageResult, QwenImageError
from models.content import Content
from models.prompt import Prompt
from models.rednote import RednoteCase, RednoteAnalysis
from models.rules import PlatformRule
from models.task import Task
from schemas.image_generation import ImagePromptDraft
from services.exceptions import ServiceUnavailableError
from workflows import nodes
from memory.services.publications import publication_source
import test_business_api as business
import test_workflow as workflow_tests
import test_image_generation as images


class FlexibleResourcesTest(unittest.TestCase):
    setUp = business.BusinessAPITest.setUp
    tearDown = business.BusinessAPITest.tearDown
    post = business.BusinessAPITest.post
    register_second_user = business.BusinessAPITest.register_second_user

    def test_case_prompt_rule_deletion_is_owned_and_historical_references_are_retained(self):
        case = self.post('/rednote/cases', {'name': 'Case', 'body': 'Source', 'link': 'https://www.xiaohongshu.com/explore/' + 'a' * 24})
        analysis = self.post('/rednote/analyses', {'cases_id': case['id'], 'content': 'Saved analysis'})
        prompt = self.post('/prompts', {'name': 'Style', 'platform': 'rednote', 'prompt_content': 'Natural'})
        rule = self.post('/rules/custom', {'platform_name': 'rednote', 'content': 'Advisory'})
        task = self.post('/tasks', {'product_id': self.product_id, 'platform_name': 'rednote', 'task_type': 'generate', 'input_text': 'Draft'})
        content = self.post('/contents', {'task_id': task['id'], 'title': 'Title', 'body': 'Saved text'})
        with Session(self.engine) as db:
            row = db.get(Content, content['id'])
            row.generation_metadata = {'prompt_id': prompt['id'], 'analysis_references': [{'analysis_id': analysis['id'], 'creative_patterns': {'hook': 'Saved hook'}}]}
            db.commit()
        owner_headers = dict(self.client.headers)
        self.register_second_user()
        paths = [f"/rednote/cases/{case['id']}", f"/prompts/{prompt['id']}", f"/rules/custom/{rule['id']}"]
        for path in paths:
            self.assertEqual(self.client.delete(path).status_code, 404)
        self.client.headers.update(owner_headers)
        for path in paths:
            self.assertEqual(self.client.delete(path).status_code, 204)
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get('/rednote/cases').json(), [])
        self.assertEqual(self.client.get('/prompts').json(), [])
        self.assertEqual(self.client.get('/rules/custom').json(), [])
        self.assertEqual(self.client.get(f"/rednote/analyses/{analysis['id']}").status_code, 404)
        saved = self.client.get(f"/contents/{content['id']}").json()
        self.assertEqual(saved['generation_metadata']['analysis_references'][0]['creative_patterns']['hook'], 'Saved hook')
        with Session(self.engine) as db:
            self.assertTrue(db.get(RednoteCase, case['id']).is_deleted)
            self.assertTrue(db.get(Prompt, prompt['id']).is_deleted)
            self.assertIsNotNone(db.get(RednoteAnalysis, analysis['id']))
        restored = self.post('/rednote/cases', {'name': 'Restored', 'link': case['link'], 'body': 'New source'})
        self.assertEqual(restored['id'], case['id'])

    def test_shared_rule_edits_and_removal_only_affect_current_account(self):
        with Session(self.engine) as db:
            shared = PlatformRule(platform_name='rednote', content='Shared reference')
            db.add(shared); db.flush(); shared_id = shared.id; db.commit()
        path = f'/rules/platform/{shared_id}'
        edited = self.client.put(path, json={'platform_name': 'rednote', 'content': 'Personal wording'})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json()['content'], 'Personal wording')
        self.assertEqual(edited.json()['version'], 2)
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get('/rules/platform').json(), [])
        own = self.post('/rules/platform', {'platform_name': 'rednote', 'content': 'Owned rule'})
        self.register_second_user()
        self.assertEqual(self.client.get(path).json()['content'], 'Shared reference')
        self.assertEqual(self.client.get(f"/rules/platform/{own['id']}").status_code, 404)
        self.assertEqual(self.client.delete(f"/rules/platform/{own['id']}").status_code, 404)


class FlexibleWorkflowTest(unittest.TestCase):
    setUp = workflow_tests.WorkflowTest.setUp
    tearDown = workflow_tests.WorkflowTest.tearDown
    post = workflow_tests.WorkflowTest.post
    start = workflow_tests.WorkflowTest.start
    review = workflow_tests.WorkflowTest.review
    generation_mocks = workflow_tests.WorkflowTest.generation_mocks

    def image_mocks(self):
        self.image_dir = patch('config.GENERATED_IMAGE_DIR', Path(self.storage.name) / 'generated_images')
        self.image_dir.start(); self.addCleanup(self.image_dir.stop)
        prompt = patch('agents.image_prompt_agent.generate_prompt', return_value=ImagePromptDraft(prompt='A calm scene', negative_prompt='No text'))
        prompt.start(); self.addCleanup(prompt.stop)
        client = patch('services.image_generation.QwenImageClient')
        image = client.start().return_value.__enter__.return_value
        self.addCleanup(client.stop)
        image.generate.return_value = ImageResult('qwen-image-2.0', 'test-request', ('https://images.example/test.png',))
        image.download.return_value = images.png()
        return image

    def test_text_only_skips_image_api_and_can_be_manually_published_with_external_images(self):
        with patch('services.image_generation.QwenImageClient') as client:
            result = self.start(output_mode='text')
            client.assert_not_called()
        self.review(self.task['id'], result['content_id'])
        self.post(self.base + '/resume', {'action': 'review'}, 200)
        record = self.post('/publish-records', {'task_id': self.task['id'], 'content_id': result['content_id'],
            'platform': 'rednote', 'account_id': self.account_id, 'publisher_backend': 'manual'})
        with patch('memory.services.publications.try_sync_queued'):
            saved = self.post(f"/publish-records/{record['id']}/result", {'publish_status': 'published',
                'account_id': self.account_id, 'platform_post_url': 'https://www.xiaohongshu.com/explore/' + 'b' * 24,
                'final_content': {'title': 'Final title', 'body': 'Actual platform text'},
                'external_materials_note': 'Product photos were added on the platform'}, 200)
        self.assertEqual(saved['publish_status'], 'published')
        with Session(self.engine) as db:
            db.info['user_id'] = self.user_id
            snapshot = publication_source(db, saved['id'])['content_snapshot']
            self.assertEqual(snapshot['body'], 'Actual platform text')
            self.assertIn('platform', snapshot['external_materials_note'])

    def test_images_only_does_not_generate_marketing_copy_and_can_be_reviewed(self):
        image = self.image_mocks()
        result = self.start(output_mode='images', image_count=1)
        content = self.client.get(f"/contents/{result['content_id']}").json()
        self.assertEqual((content['title'], content['body'], content['tags']), ('', '', []))
        self.assertEqual(len(content['image_assets']), 1)
        self.assertEqual(content['generation_metadata']['output_mode'], 'images')
        self.assertEqual((result['status'], result['task_status']), ('waiting_review', 'pending_review'))
        self.model.assert_not_called()
        image.generate.assert_called_once()
        self.review(self.task['id'], content['id'])
        self.assertEqual(self.post(self.base + '/resume', {}, 200)['status'], 'approved')

    def test_image_failure_preserves_copy_and_retry_does_not_generate_copy_again(self):
        image = self.image_mocks()
        image.generate.side_effect = QwenImageError('Image unavailable')
        self.post(self.base + '/start', {'output_mode': 'text_images'}, 503)
        self.assertEqual(len(self.client.get('/contents').json()), 1)
        self.assertEqual(self.client.get('/contents').json()[0]['body'], self.draft['body'])
        image.generate.side_effect = None
        result = self.post(self.base + '/retry', None, 200)
        self.assertEqual(result['status'], 'waiting_review')
        self.assertEqual(len(self.client.get('/contents').json()), 2)
        self.model.assert_called_once()

    def test_committed_image_step_is_reused_after_checkpoint_failure(self):
        image = self.image_mocks()
        original = nodes.generate_images
        first = True
        def checkpoint_failure(state, runtime):
            nonlocal first
            result = original(state, runtime)
            if first:
                first = False
                raise RuntimeError('simulated checkpoint interruption')
            return result
        with patch.object(nodes, 'generate_images', checkpoint_failure):
            self.post(self.base + '/start', {'output_mode': 'images'}, 503)
            result = self.post(self.base + '/retry', None, 200)
        self.assertEqual(result['status'], 'waiting_review')
        self.assertEqual(len(self.client.get('/contents').json()), 2)
        image.generate.assert_called_once()

    def test_risk_and_check_failure_do_not_block_human_approval(self):
        self.rule_agent.side_effect = DeepSeekTimeoutError('Check unavailable')
        result = self.start()
        self.assertEqual(result['rule_check']['status'], 'error')
        self.assertEqual(result['task_status'], 'pending_review')
        self.review(self.task['id'], result['content_id'])
        self.assertEqual(self.post(self.base + '/resume', {}, 200)['status'], 'approved')

    def test_check_setup_failure_is_advisory_and_same_saved_version_can_be_submitted(self):
        with patch('services.rules.check_content', side_effect=ServiceUnavailableError('Check unavailable')):
            result = self.start()
        self.assertEqual(result['task_status'], 'pending_review')
        self.assertIsNone(result['rule_check'])
        with Session(self.engine) as db:
            db.get(Task, self.task['id']).status = 'running'; db.commit()
        again = self.post(self.base + '/resume', {'action': 'submit_review'}, 200)
        self.assertEqual(again['content_id'], result['content_id'])
        self.assertEqual(again['task_status'], 'pending_review')
        self.assertEqual(len(self.client.get('/contents').json()), 1)
