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

from . import muxlisa, voice
from .speech_text import spoken_text
from .models import Department, Task, User

HTTPClient = httpx.Client


@override_settings(OPENAI_API_KEY='test-openai-key', MUXLISA_API_KEY='test-muxlisa-key', MUXLISA_SPEAKER=0,
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

    def audio_post(self):
        return self.client.post('/voice/transcribe/', {'audio': self.audio()})

    def transport(self, handler):
        def dispatch(request):
            self.requests.append(request)
            return handler(request)
        return patch('core.muxlisa.httpx.Client', side_effect=lambda **kwargs: HTTPClient(transport=httpx.MockTransport(dispatch), **kwargs))

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
        paths = ['/voice/transcribe/', '/voice/draft/', '/voice/speak/']
        self.client.logout()
        self.assertEqual(self.client.get('/voice/status/').status_code, 401)
        for path in paths:
            self.assertEqual(self.post(path, {}).status_code, 401)
        self.client.force_login(self.employee)
        self.assertEqual(self.post('/voice/draft/', {}).status_code, 403)
        self.assertEqual(self.client.get('/voice/status/').status_code, 200)
        with self.transport(lambda r: httpx.Response(200, json={'text': 'Topshiriqlarni och'})):
            self.assertEqual(self.audio_post().status_code, 200)
        with self.transport(self.tts_handler):
            self.assertEqual(self.post('/voice/speak/', {'reply_token': self.reply_token(self.employee)}).status_code, 200)

    def test_voice_post_endpoints_enforce_csrf_and_http_method(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.head)
        for path in ['/voice/transcribe/', '/voice/draft/', '/voice/speak/']:
            self.assertEqual(client.post(path).status_code, 403)
            self.assertEqual(client.get(path).status_code, 405)

    @override_settings(MUXLISA_API_KEY='')
    def test_configuration_is_independent_and_never_exposes_credentials(self):
        response = self.client.get('/voice/status/')
        self.assertEqual(response.json(), {'configured': False, 'stt': False, 'llm': True, 'tts': False,
                                         'max_audio_bytes': 5242880, 'max_seconds': 29})
        self.assertNotContains(response, 'test-openai-key')
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_stt_posts_audio_to_muxlisa_with_the_api_key_header(self):
        with self.transport(lambda r: httpx.Response(200, json={'text': 'Xalimovga ertagacha pasportlarni yangilash'})):
            self.assertEqual(self.audio_post().json()['status'], 'completed')
        request = self.requests[0]
        self.assertEqual(str(request.url), 'https://service.muxlisa.uz/api/v2/stt')
        self.assertEqual(request.headers['x-api-key'], 'test-muxlisa-key')
        self.assertIn(b'name="audio"', request.content)
        self.assertNotIn(b'Xalimov', request.content)  # No employee directory goes to STT.

    def test_unsupported_audio_is_rejected_before_provider(self):
        with patch('core.muxlisa.request') as provider:
            self.assertEqual(self.client.post('/voice/transcribe/', {'audio': self.audio('file.exe')}).status_code, 400)
            provider.assert_not_called()

    @override_settings(VOICE_MAX_AUDIO_BYTES=5)
    def test_large_audio_is_rejected(self):
        with patch('core.muxlisa.request') as provider:
            self.assertEqual(self.audio_post().status_code, 413)
            provider.assert_not_called()

    @override_settings(MUXLISA_API_KEY='')
    def test_missing_stt_key_returns_actionable_error(self):
        self.assertEqual(self.audio_post().json()['error'], 'muxlisa_not_configured')

    def test_provider_errors_do_not_leak_private_response_content(self):
        with self.transport(lambda r: httpx.Response(401, json={'detail': 'secret-key-in-error'})):
            response = self.audio_post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret-key-in-error', response.content.decode())

    def test_provider_statuses_map_to_user_errors(self):
        for status, code in [(400, 'muxlisa_invalid_audio'), (402, 'muxlisa_insufficient_credits'),
                             (429, 'muxlisa_rate_limit'), (500, 'muxlisa_unavailable')]:
            with self.subTest(status=status), self.transport(lambda r, status=status: httpx.Response(status, json={'detail': 'x'})):
                self.assertEqual(self.audio_post().json()['error'], code)

    def test_silence_and_malformed_transcripts_fail(self):
        for body in [{'text': ''}, {}, {'text': 123}]:
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
        self.assertNotIn('test-muxlisa-key', request.content.decode())

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
        with patch('core.muxlisa.request') as provider:
            response = self.post('/voice/draft/', {'command': 'Vazifa yarat'})
            self.assertEqual(response.status_code, 503)
            provider.assert_not_called()

    def test_tts_sends_the_configured_speaker_and_returns_wav(self):
        with self.transport(self.tts_handler):
            response = self.post('/voice/speak/', {'reply_token': self.reply_token()})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Content-Type'], 'audio/wav')
        self.assertIn('no-store', response['Cache-Control'])
        request = self.requests[0]
        self.assertEqual(str(request.url), 'https://service.muxlisa.uz/api/v2/tts')
        self.assertEqual(request.headers['x-api-key'], 'test-muxlisa-key')
        self.assertEqual(json.loads(request.content), {'text': 'Maydonlarni tekshiring.', 'speaker': 0})
        with override_settings(MUXLISA_SPEAKER=1), self.transport(self.tts_handler):
            self.post('/voice/speak/', {'reply_token': self.reply_token()})
        self.assertEqual(json.loads(self.requests[-1].content)['speaker'], 1)

    def test_tts_rejects_raw_text_forged_or_other_users_reply(self):
        for payload in [{'text': 'Arbitrary text'}, {'reply_token': 'forged'}, {'reply_token': self.reply_token(self.chair)}]:
            with patch('core.muxlisa.synthesize') as provider:
                self.assertIn(self.post('/voice/speak/', payload).status_code, (400, 403))
                provider.assert_not_called()

    def test_expired_reply_is_rejected(self):
        with patch('django.core.signing.time.time', return_value=1):
            token = self.reply_token()
        self.assertEqual(self.post('/voice/speak/', {'reply_token': token}).json()['error'], 'expired_ticket')

    def test_non_wav_provider_reply_is_refused(self):
        with self.transport(lambda r: httpx.Response(200, content=b'not-audio', headers={'Content-Type': 'application/json'})):
            response = self.post('/voice/speak/', {'reply_token': self.reply_token()})
        self.assertEqual(response.json()['error'], 'tts_response')

    def test_speech_excerpt_obeys_provider_character_limit(self):
        for text in ['O‘zbekcha savol. ' * 200, '界' * 1001, 'x' * 1001]:
            result = muxlisa.speech_excerpt(text)
            self.assertLessEqual(len(result), muxlisa.MAX_TTS_CHARACTERS)
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

    def test_provider_failures_never_repeat_provider_wording(self):
        with self.assertRaises(voice.VoiceError) as caught:
            muxlisa.check_response(httpx.Response(402, json={'detail': 'private-provider-data'}))
        self.assertEqual(caught.exception.code, 'muxlisa_insufficient_credits')
        self.assertEqual(caught.exception.status, 402)
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
        for key in ['test-openai-key', 'test-muxlisa-key']:
            self.assertNotContains(response, key)
