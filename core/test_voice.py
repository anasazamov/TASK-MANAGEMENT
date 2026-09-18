import io
import json
import uuid
import wave
from datetime import timedelta
from unittest.mock import patch

import httpx
from django.core import signing
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from openai import OpenAI

from . import voice, voicelab
from .speech_text import spoken_text
from .models import Department, Task, User

HTTPClient = httpx.Client


@override_settings(OPENAI_API_KEY='test-openai-key', VOICELAB_API_KEY='test-voicelab-key', VOICELAB_VOICE_ID='',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class VoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dep = Department.objects.create(name='Qurilish')
        other = Department.objects.create(name='IT')
        cls.chair = User.objects.create_user('chair', role='chair', full_name='Rais')
        cls.head = User.objects.create_user('head', role='head', full_name='Tuyliyev', department=dep)
        cls.employee = User.objects.create_user('employee', full_name='Xalimov Farrux', department=dep)
        cls.other = User.objects.create_user('other', full_name='Boshqa Xodim', department=other)

    def setUp(self):
        cache.clear()
        self.client.force_login(self.head)
        self.requests = []

    def post(self, path, data):
        return self.client.post(path, data=json.dumps(data), content_type='application/json')

    def audio(self, name='command.webm'):
        return SimpleUploadedFile(name, b'test-audio', content_type='audio/webm')

    def audio_post(self, request_id=None):
        return self.client.post('/voice/transcribe/', {'audio': self.audio(), 'request_id': request_id or str(uuid.uuid4())})

    def transport(self, handler):
        def dispatch(request):
            self.requests.append(request)
            return handler(request)
        return patch('core.voicelab.httpx.Client', side_effect=lambda **kwargs: HTTPClient(transport=httpx.MockTransport(dispatch), **kwargs))

    def result(self, **updates):
        data = dict(title='Loyiha pasportlarini yangilash', description='', assignee_id=self.employee.pk,
                    due_at=timezone.localtime(timezone.now() + timedelta(days=1)).isoformat(),
                    deadline_kind='specified', clarification='')
        return voice.TaskDraft(**{**data, **updates})

    def openai_transport(self, draft=None, status='completed'):
        output = (draft or self.result()).model_dump_json()
        def dispatch(request):
            self.requests.append(request)
            return httpx.Response(200, json={'id': 'resp_test', 'object': 'response', 'created_at': 0,
                'model': 'gpt-4.1-mini', 'status': status, 'output': [{'id': 'msg_test', 'type': 'message',
                'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': output, 'annotations': []}]}]})
        return patch('core.voice.OpenAI', side_effect=lambda **kwargs: OpenAI(http_client=HTTPClient(transport=httpx.MockTransport(dispatch)), **kwargs))

    def wav(self):
        data = io.BytesIO()
        with wave.open(data, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b'\0\0' * 240)
        return data.getvalue()

    def tts_handler(self, request):
        if request.url.path.endswith('/languages'):
            return httpx.Response(200, json={'data': [{'code': 'uz'}]})
        if request.url.path.endswith('/voices'):
            return httpx.Response(200, json={'data': [
                {'id': 'voice_private', 'language': 'uz', 'kind': 'custom'},
                {'id': 'voice_uz', 'language': 'uz', 'kind': 'system'}]})
        return httpx.Response(200, content=self.wav(), headers={'Content-Type': 'audio/wav'})

    def reply_token(self, user=None, text='Maydonlarni tekshiring.'):
        return signing.dumps({'user': (user or self.head).pk, 'text': text, 'id': 'tts-' + uuid.uuid4().hex}, salt='voice-tts')

    @override_settings(OPENAI_TASK_MODEL='gpt-5.6-sol', OPENAI_TASK_REASONING='low')
    def test_draft_uses_configured_reasoning_and_supports_legacy_model(self):
        for model in ('gpt-5.6-sol', 'gpt-4.1-mini'):
            with self.subTest(model=model), override_settings(OPENAI_TASK_MODEL=model), self.openai_transport():
                result = voice.draft_task(self.head, 'Xalimovga hisobot tayyorlash, ertaga', {})
            self.assertEqual(result['draft']['assignee_id'], self.employee.pk)
            payload = json.loads(self.requests[-1].content)
            self.assertEqual(payload['model'], model)
            self.assertFalse(payload['store'])
            if model == 'gpt-5.6-sol':
                self.assertEqual(payload['reasoning'], {'effort': 'low'})
            else:
                self.assertNotIn('reasoning', payload)

    def test_voice_endpoints_require_login_but_employees_can_use_speech(self):
        paths = ['/voice/transcribe/', '/voice/draft/', '/voice/speak/', '/voice/transcription-status/']
        self.client.logout()
        self.assertEqual(self.client.get('/voice/status/').status_code, 401)
        for path in paths:
            self.assertEqual(self.post(path, {}).status_code, 401)
        self.client.force_login(self.employee)
        self.assertEqual(self.post('/voice/draft/', {}).status_code, 403)
        self.assertEqual(self.client.get('/voice/status/').status_code, 200)
        with self.transport(lambda r: httpx.Response(200, json={'transcript': 'Topshiriqlarni och'})):
            self.assertEqual(self.audio_post().status_code, 200)
        with self.transport(self.tts_handler):
            self.assertEqual(self.post('/voice/speak/', {'reply_token': self.reply_token(self.employee)}).status_code, 200)

    def test_voice_post_endpoints_enforce_csrf_and_http_method(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.head)
        for path in ['/voice/transcribe/', '/voice/draft/', '/voice/speak/', '/voice/transcription-status/']:
            self.assertEqual(client.post(path).status_code, 403)
            self.assertEqual(client.get(path).status_code, 405)

    @override_settings(VOICELAB_API_KEY='')
    def test_configuration_is_independent_and_never_exposes_credentials(self):
        response = self.client.get('/voice/status/')
        self.assertEqual(response.json(), {'configured': False, 'stt': False, 'llm': True, 'tts': False,
                                         'max_audio_bytes': 10485760, 'max_seconds': 29})
        self.assertNotContains(response, 'test-openai-key')
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_stt_uses_voicelab_uzbek_and_reuses_idempotency_key(self):
        request_id = str(uuid.uuid4())
        with self.transport(lambda r: httpx.Response(200, json={'transcript': 'Xalimovga ertagacha pasportlarni yangilash'})):
            self.assertEqual(self.audio_post(request_id).json()['status'], 'completed')
            self.assertEqual(self.audio_post(request_id).status_code, 200)
        first, second = self.requests
        self.assertEqual(str(first.url), 'https://api.voicelab.uz/v1/stt')
        self.assertEqual(first.headers['Authorization'], 'Bearer test-voicelab-key')
        self.assertEqual(first.headers['Idempotency-Key'], second.headers['Idempotency-Key'])
        uuid.UUID(first.headers['Idempotency-Key'])
        self.assertIn(b'name="language"\r\n\r\nuz', first.content)
        self.assertIn(b'name="audio"', first.content)
        self.assertNotIn(b'Xalimov', first.content)  # No employee directory goes to STT.

    def test_invalid_audio_and_request_id_are_rejected_before_provider(self):
        with patch('core.voicelab.request') as provider:
            self.assertEqual(self.audio_post('bad').status_code, 400)
            response = self.client.post('/voice/transcribe/', {'audio': self.audio('file.exe'), 'request_id': str(uuid.uuid4())})
            self.assertEqual(response.status_code, 400)
            provider.assert_not_called()

    @override_settings(VOICE_MAX_AUDIO_BYTES=5)
    def test_large_audio_is_rejected(self):
        with patch('core.voicelab.request') as provider:
            self.assertEqual(self.audio_post().status_code, 413)
            provider.assert_not_called()

    @override_settings(VOICELAB_API_KEY='')
    def test_missing_stt_key_returns_actionable_error(self):
        self.assertEqual(self.audio_post().json()['error'], 'voicelab_not_configured')

    def test_provider_errors_do_not_leak_private_response_content(self):
        with self.transport(lambda r: httpx.Response(401, json={'message': 'secret-key-in-error', 'error': {'code': 'invalid_api_key'}})):
            response = self.audio_post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret-key-in-error', response.content.decode())

    def test_queued_audio_poll_is_bound_to_user_and_handles_completion(self):
        with self.transport(lambda r: httpx.Response(202, json={'id': 'stt_job', 'status': 'queued'})):
            result = self.audio_post()
        self.assertEqual(result.status_code, 202)
        ticket = result.json()['ticket']
        self.client.force_login(self.chair)
        with patch('core.voicelab.transcription_status') as provider:
            self.assertEqual(self.post('/voice/transcription-status/', {'ticket': ticket}).status_code, 403)
            provider.assert_not_called()
        self.client.force_login(self.head)
        with self.transport(lambda r: httpx.Response(200, json={'status': 'completed', 'transcript': 'Tayyor matn'})):
            self.assertEqual(self.post('/voice/transcription-status/', {'ticket': ticket}).json()['transcript'], 'Tayyor matn')
        self.assertEqual(self.requests[-1].url.path, '/v1/stt/transcriptions/stt_job')

    def test_silence_and_malformed_transcripts_fail(self):
        for body in [{'transcript': ''}, {}, {'transcript': 123}]:
            with self.transport(lambda r: httpx.Response(200, json=body)):
                self.assertGreaterEqual(self.audio_post().status_code, 400)

    def test_openai_receives_only_allowed_assignees_and_returns_draft_without_saving(self):
        with self.openai_transport():
            response = self.post('/voice/draft/', {'command': 'Xalimovga pasportlarni ertagacha yangilash', 'current': {}})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['draft']['assignee_id'], self.employee.pk)
        self.assertIn('reply_token', response.json())
        self.assertEqual(Task.objects.count(), 0)
        request = self.requests[0]
        self.assertEqual(str(request.url), 'https://api.openai.com/v1/responses')
        self.assertEqual(request.headers['Authorization'], 'Bearer test-openai-key')
        payload = json.loads(request.content)
        self.assertFalse(payload['store'])
        self.assertEqual(payload['text']['format']['type'], 'json_schema')
        context = json.loads(payload['input'][1]['content'])
        self.assertEqual([p['id'] for p in context['allowed_assignees']], [self.employee.pk])
        self.assertNotIn('Boshqa Xodim', request.content.decode())
        self.assertNotIn('test-voicelab-key', request.content.decode())

    def test_agent_cannot_assign_outside_user_scope(self):
        with self.openai_transport(self.result(assignee_id=self.other.pk)):
            response = self.post('/voice/draft/', {'command': 'Boshqa xodimga topshir'})
        self.assertIsNone(response.json()['draft']['assignee_id'])
        self.assertIn('Ijrochi kim', response.json()['message'])

    def test_agent_past_deadline_and_missing_timezone_are_not_accepted(self):
        for date in ['2020-01-01T18:00:00+05:00', '2099-01-01T18:00:00']:
            with self.openai_transport(self.result(due_at=date)):
                response = self.post('/voice/draft/', {'command': 'Muddatni belgilash'})
            self.assertEqual(response.json()['draft']['due_at'], '')
            self.assertEqual(response.json()['draft']['deadline_kind'], 'unspecified')

    def test_parent_deadline_rule_applies_to_ai_results(self):
        parent = Task.objects.create(title='Loyiha', issuer=self.chair, assignee=self.head, due_at=timezone.now()+timedelta(hours=1))
        with self.openai_transport():
            response = self.post('/voice/draft/', {'command': 'Taqsimla', 'parent_id': parent.pk})
        self.assertEqual(response.json()['draft']['deadline_kind'], 'unspecified')
        self.assertIn('asosiy topshiriq', response.json()['message'])

    def test_foreign_parent_blocks_ai_request(self):
        parent = Task.objects.create(title='Yopiq', issuer=self.chair, assignee=self.other)
        with patch('core.voice.OpenAI') as provider:
            self.assertEqual(self.post('/voice/draft/', {'command': 'Taqsimla', 'parent_id': parent.pk}).status_code, 404)
            provider.assert_not_called()

    def test_correction_context_is_preserved(self):
        current = dict(title='Pasportlar', description='Talablar', assignee_id=self.employee.pk,
                       due_at='', deadline_kind='none')
        with self.openai_transport(self.result(due_at=None, deadline_kind='none')):
            response = self.post('/voice/draft/', {'command': 'Muddatsiz qolsin', 'current': current})
        context = json.loads(json.loads(self.requests[0].content)['input'][1]['content'])
        self.assertEqual(context['current_draft'], current)
        self.assertEqual(response.json()['draft']['deadline_kind'], 'none')

    @override_settings(OPENAI_API_KEY='')
    def test_missing_llm_key_does_not_fall_back_to_other_provider(self):
        with patch('core.voicelab.request') as provider:
            response = self.post('/voice/draft/', {'command': 'Vazifa yarat'})
            self.assertEqual(response.status_code, 503)
            provider.assert_not_called()

    def test_tts_uses_catalog_uzbek_voice_and_same_key_for_retry(self):
        token = self.reply_token()
        with self.transport(self.tts_handler):
            for _ in range(2):
                response = self.post('/voice/speak/', {'reply_token': token})
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response['Content-Type'], 'audio/wav')
                self.assertIn('no-store', response['Cache-Control'])
        generations = [r for r in self.requests if r.url.path == '/v1/tts']
        self.assertEqual(len(generations), 2)
        self.assertEqual(generations[0].headers['Idempotency-Key'], generations[1].headers['Idempotency-Key'])
        self.assertEqual(json.loads(generations[0].content), {'text': 'Maydonlarni tekshiring.', 'language': 'uz', 'voice_id': 'voice_uz', 'speed': 1})
        self.assertEqual(len(self.requests), 4)  # Language/voice catalogs are cached.

    def test_tts_rejects_raw_text_forged_or_other_users_reply(self):
        for payload in [{'text': 'Arbitrary text'}, {'reply_token': 'forged'}, {'reply_token': self.reply_token(self.chair)}]:
            with patch('core.voicelab.synthesize') as provider:
                self.assertIn(self.post('/voice/speak/', payload).status_code, (400, 403))
                provider.assert_not_called()

    def test_expired_reply_is_rejected(self):
        with patch('django.core.signing.time.time', return_value=1):
            token = self.reply_token()
        self.assertEqual(self.post('/voice/speak/', {'reply_token': token}).json()['error'], 'expired_ticket')

    @override_settings(VOICELAB_VOICE_ID='voice_missing')
    def test_invalid_voice_does_not_generate_speech(self):
        with self.transport(self.tts_handler):
            response = self.post('/voice/speak/', {'reply_token': self.reply_token()})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(any(r.url.path == '/v1/tts' for r in self.requests))

    def test_utf8_speech_excerpt_obeys_provider_byte_limit(self):
        for text in ['O‘zbekcha savol. ' * 200, '界' * 1000, 'x' * 1001]:
            result = voicelab.speech_excerpt(text)
            self.assertLessEqual(len(result.encode('utf-8')), 1000)
            self.assertTrue(result.endswith('ekrandan o‘qing.'))

    def test_speech_copy_expands_task_codes_times_and_omits_markdown(self):
        text = '**T-118** — [A’zamov Aziz](/tasks/18/). Soat 18:30 gacha.'
        self.assertEqual(spoken_text(text), "yuz o'n sakkiz raqamli topshiriq — A'zamov Aziz. soat o'n sakkiz o'ttizgacha.")
        self.assertEqual(spoken_text('T-104ni och. 09:00.'), "yuz to'rt raqamli topshiriqni och. soat to'qqiz.")
        self.assertEqual(spoken_text('Azizga 2 ta ish.'), 'Azizga ikki ta ish.')

    def test_spoken_dates_counts_acronyms_and_list_boundaries(self):
        self.assertEqual(spoken_text('Muddat: 18.09.2026 soat 17:30.'),
            "Muddat: ikki ming yigirma oltinchi yil, o'n sakkizinchi sentabr soat o'n yetti o'ttiz.")
        self.assertEqual(spoken_text('IT bo‘limi, ERP tizimi. Jami 21 ta. 2-sahifa.'),
            "ay ti bo'limi, i ar pi tizimi. Jami yigirma bir ta. ikkinchi sahifa.")
        self.assertEqual(spoken_text('Nomzodlar:\n1. A’zamov Aziz — IT\n2. Kamolov Akmal — Moliya'),
            "Nomzodlar: A'zamov Aziz — ay ti. Kamolov Akmal — Moliya")
        self.assertEqual(spoken_text('31.02.2026; 2.5 ta; A-123; 123ABC; item.'),
            '31.02.2026; 2.5 ta; A-123; 123ABC; item.')

    def test_failed_stt_job_reports_provider_overload_without_blame_or_details(self):
        with self.assertRaises(voice.VoiceError) as caught:
            voicelab.transcription_result({'status': 'failed', 'error': {
                'code': 'stt_overloaded', 'stage': 'scheduler', 'message': 'private-provider-data'}})
        self.assertEqual(caught.exception.code, 'voicelab_overloaded')
        self.assertEqual(caught.exception.status, 503)
        self.assertIn('xizmati band', caught.exception.message)
        self.assertNotIn('private-provider-data', caught.exception.message)

    @override_settings(VOICE_REQUESTS_PER_MINUTE=1)
    def test_paid_request_rate_limit(self):
        with self.openai_transport():
            self.assertEqual(self.post('/voice/draft/', {'command': 'Pasportlarni yangila'}).status_code, 200)
            self.assertEqual(self.post('/voice/draft/', {'command': 'Yana'}).status_code, 429)
        self.assertEqual(len(self.requests), 1)

    def test_voice_form_renders_all_three_provider_flows_without_secrets(self):
        response = self.client.get('/tasks/new/')
        for text in ['voice/speak/', 'voice/transcribe/', 'agent/message/', 'Javobni ovozda o‘qish']:
            self.assertContains(response, text)
        for key in ['test-openai-key', 'test-voicelab-key']:
            self.assertNotContains(response, key)
