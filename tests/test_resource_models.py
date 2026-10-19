"""账号连接、默认模型选择、向量空间隔离及生图结果的集成测试。"""
import base64
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from cryptography.fernet import Fernet
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from models.resource_model import UserResourceModel
from models.document_chunk import DocumentChunk
from rag import retriever
from rag.vector_store import SearchResult
from services import resource_models
from services.exceptions import InvalidInputError, NotFoundError, ServiceUnavailableError
from integrations.llm.resource_clients import ResourceEmbeddings, ResourceImageClient, request_json
from pydantic import SecretStr
import test_business_api as business
import test_image_generation as images

EMBEDDING = {'kind': 'embedding', 'name': 'Vector', 'provider': 'Any supplier', 'model': 'custom-embedding',
    'base_url': 'https://api.vendor.example/v1', 'api_key': 'test-secret-vector', 'dimensions': 3, 'is_default': True}
IMAGE = {'kind': 'image', 'name': 'Images', 'provider': 'Image supplier', 'model': 'custom-image',
    'base_url': 'https://images.vendor.example/v1', 'api_key': 'test-secret-image',
    'image_sizes': ['1024*1024'], 'is_default': True}


class ResourceModelsAPITest(unittest.TestCase):
    tearDown = business.BusinessAPITest.tearDown
    post = business.BusinessAPITest.post
    register_second_user = business.BusinessAPITest.register_second_user

    def setUp(self):
        business.BusinessAPITest.setUp(self)
        p = patch('config.MODEL_CREDENTIALS_KEY', Fernet.generate_key().decode()); p.start(); self.addCleanup(p.stop)

    def test_independent_defaults_encryption_ownership_and_deletion(self):
        vector = self.post('/resource-models', EMBEDDING)
        image = self.post('/resource-models', IMAGE)
        self.assertEqual([row['id'] for row in self.client.get('/resource-models?kind=embedding').json()], [vector['id']])
        with Session(self.engine) as db:
            db.info['user_id'] = self.user_id
            self.assertEqual(resource_models.select_profile(db, 'embedding').id, vector['id'])
            self.assertEqual(resource_models.select_profile(db, 'image').id, image['id'])
            self.assertNotIn(EMBEDDING['api_key'], db.get(UserResourceModel, vector['id']).encrypted_api_key)
        self.assertNotIn('test-secret', self.client.get('/resource-models').text)
        second = self.post('/resource-models', {**EMBEDDING, 'name': 'Other vector'})
        self.assertEqual([r['id'] for r in self.client.get('/resource-models').json() if r['is_default']], [image['id'], second['id']])
        owner = dict(self.client.headers)
        self.register_second_user()
        self.assertEqual(self.client.get('/resource-models').json(), [])
        self.assertEqual(self.client.delete(f"/resource-models/{vector['id']}").status_code, 404)
        self.assertEqual(self.client.put(f"/resource-models/{vector['id']}", json=EMBEDDING).status_code, 404)
        self.client.headers.update(owner)
        self.assertEqual(self.client.delete(f"/resource-models/{second['id']}").status_code, 204)
        with Session(self.engine) as db:
            db.info['user_id'] = self.user_id
            self.assertIsNone(db.get(UserResourceModel, second['id']))
            self.assertIsNone(resource_models.select_profile(db, 'embedding'))

    def test_update_retains_key_and_only_embedding_identity_changes_namespace(self):
        row = self.post('/resource-models', EMBEDDING)
        def profile():
            with Session(self.engine) as db:
                db.info['user_id'] = self.user_id
                return resource_models.select_profile(db, 'embedding')
        original = profile()
        updated = {**EMBEDDING, 'api_key': None, 'name': 'Renamed'}
        self.assertEqual(self.client.put(f"/resource-models/{row['id']}", json=updated).status_code, 200)
        self.assertEqual(profile().embedding_space, original.embedding_space)
        changed = {**updated, 'base_url': 'https://other.vendor.example/v1'}
        self.assertEqual(self.client.put(f"/resource-models/{row['id']}", json=changed).status_code, 422)
        self.assertEqual(self.client.put(f"/resource-models/{row['id']}", json={**changed, 'api_key': 'new-secret'}).status_code, 200)
        self.assertNotEqual(profile().embedding_space, original.embedding_space)
        self.assertNotIn('api_key', json.dumps(profile().snapshot()))
        self.assertNotIn('new-secret', repr(profile()))

    def test_validation_never_echoes_credentials_and_capabilities_cannot_be_changed(self):
        for data in ({**EMBEDDING, 'dimensions': None}, {**EMBEDDING, 'dimensions': 0},
                     {**EMBEDDING, 'base_url': 'https://127.0.0.1'}, {**EMBEDDING, 'protocol': 'qwen_image'},
                     {**IMAGE, 'image_sizes': []}, {**IMAGE, 'dimensions': 10},
                     {**IMAGE, 'base_url': 'https://api.vendor.example/v1/images/generations'}):
            response = self.client.post('/resource-models', json=data)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn(data['api_key'], response.text)
        row = self.post('/resource-models', EMBEDDING)
        self.assertEqual(self.client.put(f"/resource-models/{row['id']}", json=IMAGE).status_code, 422)
        self.client.headers.pop('Authorization')
        self.assertEqual(self.client.get('/resource-models').status_code, 401)

    def test_default_embedding_is_used_for_index_and_query_and_mismatch_requires_reindex(self):
        row = self.post('/resource-models', EMBEDDING)
        upload = self.client.post(f'/products/{self.product_id}/documents', files={'file': ('manual.txt', b'Product facts', 'text/plain')})
        self.assertEqual(upload.status_code, 201, upload.text)
        doc_id = upload.json()['id']
        def payload(url, key, data, **kwargs):
            self.assertEqual(key, EMBEDDING['api_key'])
            return {'model': 'custom-embedding', 'data': [{'index': i, 'embedding': [1., 0., 0.]} for i, _ in enumerate(data['input'])]}
        with patch('integrations.llm.resource_clients.request_json', side_effect=payload) as call:
            indexed = self.post(f'/products/{self.product_id}/rag/documents/{doc_id}/index', {}, 200)
            self.assertEqual(indexed['model'], 'custom-embedding')
            with Session(self.engine) as db:
                stored = db.scalar(select(DocumentChunk)); space = stored.embedding_space
                self.assertEqual(len(space), 64)
                db.info['user_id'] = self.user_id
                hit = SearchResult(stored.chunk_id, stored.text, self.user_id, self.product_id, doc_id, 'manual.txt', 0, 1., stored.chunk_metadata)
                with patch('rag.retriever.search_vectors', return_value=[hit]) as search:
                    result = retriever.retrieve(db, self.product_id, 'Product')
                self.assertTrue(result.context)
                self.assertEqual(search.call_args.kwargs['embedding_space'], space)
            self.assertEqual(call.call_count, 2)
        self.client.put(f"/resource-models/{row['id']}", json={**EMBEDDING, 'model': 'another-embedding'})
        with Session(self.engine) as db, patch('integrations.llm.resource_clients.request_json') as call:
            db.info['user_id'] = self.user_id
            with self.assertRaisesRegex(InvalidInputError, '重新入库'):
                retriever.retrieve(db, self.product_id, 'Product')
            call.assert_not_called()
            self.assertEqual(db.scalar(select(DocumentChunk)).embedding_space, space)


class ResourceClientTest(unittest.TestCase):
    def profile(self, kind='embedding'):
        return resource_models.ResourceProfile(1, kind, 'Vendor', 'custom-model', 'compatible',
            'https://api.vendor.example/v1', 3 if kind == 'embedding' else None, True,
            ('1024*1024',), SecretStr('private-api-key'))

    def test_embedding_protocol_and_dimension_validation(self):
        def handler(request):
            value = json.loads(request.content)
            self.assertTrue(str(request.url).endswith('/embeddings'))
            self.assertEqual(value['dimensions'], 3)
            self.assertEqual(request.headers['Authorization'], 'Bearer private-api-key')
            return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1., 0., 0.]}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with ResourceEmbeddings(self.profile(), http_client=client) as embedder:
                self.assertEqual(embedder.embed_query('query'), [1., 0., 0.])
                self.assertEqual(len(embedder.embedding_space), 64)
        with patch('integrations.llm.resource_clients.request_json', return_value={'data': [{'index': 0, 'embedding': [1.]}]}):
            with self.assertRaises(ServiceUnavailableError):
                ResourceEmbeddings(self.profile()).embed_query('query')

    def test_image_base64_protocol_and_public_image_download_do_not_forward_credentials(self):
        encoded = base64.b64encode(images.png()).decode()
        profile = replace(self.profile('image'), image_sizes=('8*8',))
        def handler(request):
            self.assertEqual(json.loads(request.content)['size'], '8x8')
            return httpx.Response(200, json={'data': [{'b64_json': encoded}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, ResourceImageClient(profile, http_client=client) as image:
            result = image.generate('Scene', 'No text', size='8*8')
            self.assertEqual(image.download(result.urls[0], size='8*8'), images.png())
        with patch('integrations.llm.resource_clients.PublicHTTPSTransport', return_value=httpx.MockTransport(
                lambda request: httpx.Response(200, content=images.png()) if 'authorization' not in request.headers else self.fail('Leaked API key'))):
            self.assertEqual(ResourceImageClient(profile).download('https://images.vendor.example/test.png', size='8*8'), images.png())
        with self.assertRaises(InvalidInputError):
            ResourceImageClient(profile).download('https://127.0.0.1/test.png', size='8*8')

    def test_native_qwen_protocol_uses_user_credentials_and_model(self):
        profile = replace(self.profile('image'), protocol='qwen_image', model='configured-qwen-image',
                          base_url='https://dashscope.aliyuncs.com', image_sizes=('1728*2368',))
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload['model'], 'configured-qwen-image')
            self.assertEqual(payload['parameters']['size'], '1728*2368')
            self.assertEqual(request.headers['Authorization'], 'Bearer private-api-key')
            self.assertTrue(request.url.path.endswith('/multimodal-generation/generation'))
            return httpx.Response(200, json={'output': {'choices': [{'message': {'content': [{'image': images.URL}]}}]}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, ResourceImageClient(profile, http_client=client) as image:
            self.assertEqual(image.generate('Scene', 'No text', size='1728*2368').urls, (images.URL,))

    def test_errors_hide_provider_bodies_and_timeout_is_not_retried(self):
        for response in (httpx.Response(401, text='secret-provider-output'), httpx.Response(302, headers={'location': 'http://127.0.0.1'})):
            with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
                with self.assertRaises(ServiceUnavailableError) as error:
                    request_json('https://api.vendor.example/v1/embeddings', 'private-api-key', {}, client=client)
                self.assertNotIn('secret-provider-output', str(error.exception))
        calls = []
        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout('private details')
        with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
            with self.assertRaises(ServiceUnavailableError):
                request_json('https://api.vendor.example/v1/images/generations', 'private-api-key', {}, client=client)
        self.assertEqual(len(calls), 1)


class ResourceImageSavingTest(unittest.TestCase):
    setUpBase = business.BusinessAPITest.setUp
    setUp = images.ImageAPITest.setUp
    tearDown = business.BusinessAPITest.tearDown
    post = business.BusinessAPITest.post
    status = business.BusinessAPITest.status
    task_and_content = business.BusinessAPITest.task_and_content
    generation_mocks = business.BusinessAPITest.generation_mocks

    def test_custom_default_image_client_saves_owned_version_and_records_model(self):
        with patch('config.MODEL_CREDENTIALS_KEY', Fernet.generate_key().decode()):
            connection = self.post('/resource-models', IMAGE)
            with patch('integrations.llm.resource_clients.ResourceImageClient') as factory:
                image = factory.return_value.__enter__.return_value
                image.generate.return_value = images.ImageResult('custom-image', 'request-id', ('inline:0',))
                image.download.return_value = images.png()
                result = self.post(self.endpoint, {'size': '1024*1024'})
                self.assertEqual(factory.call_args.args[0].id, connection['id'])
        saved = result['content']
        self.assertEqual(saved['version'], 2)
        self.assertEqual(saved['generation_metadata']['image_generation']['image_model_profile']['id'], connection['id'])
        self.assertNotIn('test-secret-image', json.dumps(saved))
        self.assertEqual(self.client.get(f"/contents/{saved['id']}/images/0").status_code, 200)

    def test_custom_image_failure_preserves_original_saved_content(self):
        with patch('config.MODEL_CREDENTIALS_KEY', Fernet.generate_key().decode()):
            self.post('/resource-models', IMAGE)
            with patch('integrations.llm.resource_clients.ResourceImageClient') as factory:
                factory.return_value.__enter__.return_value.generate.side_effect = ServiceUnavailableError('Image unavailable')
                self.post(self.endpoint, {'size': '1024*1024'}, 503)
        self.assertEqual(len(self.client.get('/contents').json()), 1)
        self.assertEqual(self.client.get(f'/contents/{self.content_id}').json()['body'], 'Body')
