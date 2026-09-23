import asyncio
import io
import json
import wave
from unittest.mock import AsyncMock, patch

import httpx
from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings

from . import live, voice
from .models import User

HTTPClient = httpx.AsyncClient


def wav(rate=24000, frames=b'\x01\0'*240):
    data = io.BytesIO()
    with wave.open(data, 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(frames)
    return data.getvalue()


def transport(handler):
    return patch('core.live.httpx.AsyncClient',
                 side_effect=lambda **kwargs: HTTPClient(transport=httpx.MockTransport(handler), **kwargs))


@override_settings(MUXLISA_API_KEY='test-private-key', MUXLISA_SPEAKER=0)
class SpeechStreamTests(SimpleTestCase):
    def collect(self, handler, text='Salom'):
        async def run():
            return [json.loads(chunk) async for chunk in live.speech_stream(text)]
        with transport(handler):
            return async_to_sync(run)()

    def test_wav_reply_is_streamed_as_pcm_with_its_own_sample_rate(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(200, content=wav(22050), headers={'Content-Type': 'audio/wav'})
        events = self.collect(handle)
        self.assertEqual([item['type'] for item in events], ['ready', 'audio', 'done'])
        self.assertEqual(events[0]['sample_rate'], 22050)
        self.assertEqual(len(calls), 1)
        self.assertEqual(str(calls[0].url), 'https://service.muxlisa.uz/api/v2/tts')
        self.assertEqual(calls[0].headers['x-api-key'], 'test-private-key')
        self.assertEqual(json.loads(calls[0].content), {'text': 'Salom', 'speaker': 0})

    def test_provider_errors_are_specific_redacted_and_never_retried(self):
        for status, code in [(402, 'tts_insufficient_credits'), (403, 'tts_forbidden'),
                             (401, 'tts_invalid_api_key'), (429, 'tts_rate_limit'),
                             (400, 'tts_validation_error'), (500, 'tts_connection_failed')]:
            calls = []
            def handle(request, status=status):
                calls.append(request)
                return httpx.Response(status, json={'detail': 'test-private-key'})
            with self.subTest(status=status):
                events = self.collect(handle)
                self.assertEqual(events[0]['code'], code)
                self.assertNotIn('test-private-key', json.dumps(events))
                self.assertEqual(len(calls), 1)

    def test_broken_audio_reply_is_reported_without_playing(self):
        for content, content_type in [(b'not-audio', 'audio/wav'), (wav(), 'application/json'),
                                      (wav(rate=4000), 'audio/wav'), (wav()[:20], 'audio/wav')]:
            with self.subTest(content_type=content_type):
                events = self.collect(lambda r: httpx.Response(200, content=content, headers={'Content-Type': content_type}))
                self.assertEqual([item['type'] for item in events], ['error'])

    @override_settings(MUXLISA_API_KEY='')
    def test_missing_key_and_long_text_never_reach_the_provider(self):
        provider = AsyncMock()
        with patch('core.live.synthesize_speech', provider):
            self.assertEqual(self.collect(None)[0]['code'], 'muxlisa_not_configured')
            with override_settings(MUXLISA_API_KEY='test-private-key'):
                self.assertEqual(self.collect(None, 'x' * 1001)[0]['code'], 'speech_text')
        provider.assert_not_awaited()

    def test_cancelling_playback_cancels_the_http_request(self):
        async def run():
            requested, cancelled = asyncio.Event(), asyncio.Event()
            async def handle(request):
                requested.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            with transport(handle):
                stream = live.speech_stream('Salom')
                pending = asyncio.create_task(anext(stream))
                await asyncio.wait_for(requested.wait(), 1)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
                self.assertTrue(cancelled.is_set())
                await stream.aclose()
        async_to_sync(run)()


# These cover the speech relay itself; the judgment that decides whether an
# utterance was addressed to us is off here and tested in test_typesafe.
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   MUXLISA_API_KEY='test-private-key', TYPESAFE_API_KEY='',
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class RealtimeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('speaker', full_name='Xodim', role='employee')
        self.client.force_login(self.user)

    def ticket(self):
        response = self.client.post('/voice/realtime-session/')
        self.assertEqual(response.status_code, 200)
        return response.json()['ticket']

    def scope(self, origin='http://testserver'):
        return {'type': 'websocket', 'path': '/voice/live-stream/', 'query_string': b'',
            'headers': [(b'origin', origin.encode()), (b'cookie',
                f'{settings.SESSION_COOKIE_NAME}={self.client.cookies[settings.SESSION_COOKIE_NAME].value}'.encode())]}

    def speak(self, pcm=b'\0\0'*16000, text='{"type":"commit"}'):
        async def run():
            app = ApplicationCommunicator(live.relay, self.scope())
            await app.send_input({'type': 'websocket.connect'})
            await app.receive_output()
            await app.send_input({'type': 'websocket.receive', 'text': json.dumps({'ticket': 'signed'})})
            ready = json.loads((await app.receive_output())['text'])
            self.assertEqual(ready['sample_rate'], 16000)
            await app.send_input({'type': 'websocket.receive', 'text': json.dumps({'type': 'start'})})
            await app.send_input({'type': 'websocket.receive', 'bytes': pcm})
            await app.send_input({'type': 'websocket.receive', 'text': text})
            self.assertEqual(json.loads((await app.receive_output())['text'])['event'], 'recognizing')
            result = json.loads((await app.receive_output())['text'])
            await app.wait()
            return result
        with patch('core.live.authenticate', return_value={'user_id': self.user.pk}):
            return async_to_sync(run)()

    def test_ticket_requires_login_csrf_and_never_contains_provider_key(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post('/voice/realtime-session/').status_code, 403)
        response = self.client.post('/voice/realtime-session/')
        self.assertNotContains(response, 'test-private-key')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.json()['websocket_path'], '/voice/live-stream/')
        self.client.logout()
        self.assertEqual(self.client.post('/voice/realtime-session/').status_code, 401)

    def test_ticket_is_single_use_same_origin_and_session_bound(self):
        ticket = self.ticket()
        self.assertFalse(live.authenticate(self.scope('https://evil.invalid'), ticket))
        self.assertTrue(live.authenticate(self.scope(), ticket))
        self.assertFalse(live.authenticate(self.scope(), ticket))
        ticket = self.ticket()
        scope = self.scope(); scope['headers'] = [(b'origin', b'http://testserver')]
        self.assertFalse(live.authenticate(scope, ticket))

    def test_expired_ticket_and_deactivated_user_are_rejected(self):
        with patch('django.core.signing.time.time', return_value=1): ticket = self.ticket()
        self.assertFalse(live.authenticate(self.scope(), ticket))
        ticket = self.ticket()
        self.user.is_active = False; self.user.save()
        self.assertFalse(live.authenticate(self.scope(), ticket))

    def test_stream_requires_signed_own_reply(self):
        for body in [{'text': 'Arbitrary'}, {'reply_token': 'forged'},
                     {'reply_token': signing.dumps({'user': self.user.pk+1, 'text': 'Secret', 'id': 'tts-test'}, salt='voice-tts')}]:
            response = self.client.post('/voice/realtime-speak/', json.dumps(body), content_type='application/json')
            self.assertIn(response.status_code, (400, 403))

    @override_settings(OPENAI_API_KEY='vlk_wrong-provider-test')
    def test_speech_provider_key_is_never_sent_to_openai(self):
        self.assertFalse(voice.configured())
        with patch('core.voice.OpenAI') as provider:
            with self.assertRaises(voice.VoiceError) as error:
                with voice.provider(): pass
        self.assertEqual(error.exception.code, 'wrong_provider_key')
        provider.assert_not_called()

    def test_socket_rejects_unauthenticated_frame_before_provider_call(self):
        async def run():
            app = ApplicationCommunicator(live.relay, self.scope())
            await app.send_input({'type': 'websocket.connect'})
            self.assertEqual((await app.receive_output())['type'], 'websocket.accept')
            await app.send_input({'type': 'websocket.receive', 'text': json.dumps({'ticket': 'forged'})})
            self.assertEqual((await app.receive_output())['code'], 4403)
            await app.wait()
        with patch('core.live.transcribe_utterance') as provider:
            async_to_sync(run)()
        provider.assert_not_called()

    def test_utterance_is_sent_once_as_wav_and_only_final_text_returns(self):
        seen = []
        def dispatch(request):
            seen.append(request)
            body = request.read()
            with wave.open(io.BytesIO(body[body.index(b'RIFF'):]), 'rb') as audio:
                self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getsampwidth()), (16000, 1, 2))
                self.assertEqual(audio.readframes(16000), b'\1\0'*16000)
            return httpx.Response(200, json={'text': 'Topshiriqlarni och'})
        with transport(dispatch):
            self.assertEqual(self.speak(b'\1\0'*16000), {'event': 'final', 'text': 'Topshiriqlarni och'})
        self.assertEqual(len(seen), 1)
        self.assertEqual(str(seen[0].url), 'https://service.muxlisa.uz/api/v2/stt')
        self.assertEqual(seen[0].headers['x-api-key'], 'test-private-key')

    def test_provider_failures_map_to_codes_without_repeating_paid_calls(self):
        for response, code in [(httpx.Response(402, json={'detail': 'private-provider-data'}), 'insufficient_credits'),
                               (httpx.Response(429, json={'detail': 'x'}), 'rate_limit'),
                               (httpx.Response(400, json={'detail': 'x'}), 'invalid_audio'),
                               (httpx.Response(500, text='x'), 'service_unavailable'),
                               (httpx.Response(200, json={'text': '  '}), 'no_speech_detected'),
                               (httpx.Response(200, json={'ok': 1}), 'service_unavailable')]:
            calls = []
            def dispatch(request, response=response, calls=calls):
                calls.append(request)
                return response
            with self.subTest(code=code), transport(dispatch):
                result = self.speak()
            self.assertEqual(result, {'event': 'error', 'code': code})
            self.assertEqual(len(calls), 1)
            self.assertNotIn('private-provider-data', json.dumps(result))

    def test_too_short_or_too_long_audio_never_reaches_the_provider(self):
        with patch('core.live.httpx.AsyncClient') as client:
            self.assertEqual(async_to_sync(live.transcribe_utterance)(b'\0\0'*800)['code'], 'invalid_audio')
            self.assertEqual(async_to_sync(live.transcribe_utterance)(b'\0\0'*16000*61)['code'], 'invalid_audio')
        client.assert_not_called()

    def test_disconnect_cancels_pending_transcription(self):
        cancelled = []
        async def slow(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        async def run():
            app = ApplicationCommunicator(live.relay, self.scope())
            await app.send_input({'type': 'websocket.connect'}); await app.receive_output()
            await app.send_input({'type': 'websocket.receive', 'text': '{"ticket":"signed"}'}); await app.receive_output()
            await app.send_input({'type': 'websocket.receive', 'text': '{"type":"start"}'})
            await app.send_input({'type': 'websocket.receive', 'bytes': b'\0\0'*16000})
            await app.send_input({'type': 'websocket.receive', 'text': '{"type":"commit"}'})
            self.assertEqual(json.loads((await app.receive_output())['text'])['event'], 'recognizing')
            await app.send_input({'type': 'websocket.disconnect'}); await app.wait()
        with patch('core.live.authenticate', return_value={'user_id': self.user.pk}), \
                patch('core.live.transcribe_utterance', slow):
            async_to_sync(run)()
        self.assertTrue(cancelled)
