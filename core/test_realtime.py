import asyncio
import json
import io
import wave
from unittest.mock import patch, AsyncMock
import httpx

from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings

from . import live
from . import voice
from .models import User

HTTPClient = httpx.AsyncClient


class FakeProvider:
    def __init__(self, service='tts', frames=None):
        self.service = service
        self.sent = []
        self.closed = False
        self.frames = frames if frames is not None else [b'\x01\0'*240, json.dumps({'event': 'done'})]
        self.started = False

    async def __aenter__(self): return self
    async def __aexit__(self, *args): self.closed = True
    async def send(self, value): self.sent.append(value)
    async def recv(self):
        if not self.started:
            self.started = True
            return json.dumps({'event': 'ready', 'channels': 1,
                'sample_rate': 16000 if self.service == 'stt' else 24000,
                'audio_format' if self.service == 'stt' else 'format': 'pcm_s16le'})
        return self.frames.pop(0)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self.frames: raise StopAsyncIteration
        return self.frames.pop(0)


@override_settings(VOICELAB_API_KEY='test-private-key')
class TTSRecoveryTests(SimpleTestCase):
    def collect(self, provider, fallback):
        async def run(): return [json.loads(chunk) async for chunk in live.speech_stream('Salom', 'signed-reply')]
        with patch('core.live.PinnedConnection', return_value=provider), \
                patch('core.live.voicelab.uzbek_voice', return_value='voice_uz'), \
                patch('core.live.fallback_speech', fallback):
            return async_to_sync(run)()

    def test_transient_error_empty_stream_and_timeout_recover_once_before_audio(self):
        timed_out, disconnected = FakeProvider(), FakeProvider()
        timed_out.recv = AsyncMock(side_effect=TimeoutError())
        disconnected.recv = AsyncMock(side_effect=OSError())
        cases = {'unavailable': FakeProvider(frames=[json.dumps({'event': 'error', 'code': 'service_unavailable'})]),
                 'closed': FakeProvider(frames=[]), 'empty': FakeProvider(frames=[json.dumps({'event': 'done'})]),
                 'timeout': timed_out, 'disconnected': disconnected}
        for name, provider in cases.items():
            with self.subTest(case=name):
                fallback = AsyncMock(return_value=b'\x01\0'*240)
                events = self.collect(provider, fallback)
                self.assertEqual([e['type'] for e in events if e['type'] != 'ready'], ['recovering', 'audio', 'done'])
                fallback.assert_awaited_once_with('Salom', 'voice_uz', 'signed-reply')
                self.assertTrue(provider.closed)

    def test_credit_permission_and_rate_errors_are_specific_and_do_not_retry(self):
        for code, expected in [('insufficient_credits', 'insufficient_credits'),
                               ('insufficient_scope', 'forbidden'), ('invalid_api_key', 'invalid_api_key'),
                               ('rate_limited', 'rate_limit')]:
            with self.subTest(code=code):
                provider = FakeProvider()
                provider.recv = AsyncMock(return_value=json.dumps({'event': 'error', 'error': {'code': code, 'message': 'test-private-key'}}))
                fallback = AsyncMock()
                events = self.collect(provider, fallback)
                self.assertEqual(events[0]['code'], 'tts_'+expected)
                self.assertNotIn('test-private-key', json.dumps(events))
                fallback.assert_not_awaited()

    def test_partial_audio_is_not_repeated_after_disconnect(self):
        provider = FakeProvider(frames=[b'\x01\0'*240])
        fallback = AsyncMock()
        events = self.collect(provider, fallback)
        self.assertEqual([e['type'] for e in events], ['ready', 'audio', 'error'])
        self.assertEqual(events[-1]['code'], 'tts_incomplete_stream')
        fallback.assert_not_awaited()

    def wav(self, rate=24000):
        data = io.BytesIO()
        with wave.open(data, 'wb') as audio:
            audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x01\0'*240)
        return data.getvalue()

    def test_rest_fallback_converts_wav_and_binds_retry_to_reply_and_voice(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(200, content=self.wav(), headers={'Content-Type': 'audio/wav'})
        async def run():
            self.assertEqual(await live.fallback_speech('Salom', 'voice_uz', 'reply-1'), b'\x01\0'*240)
            await live.fallback_speech('Salom', 'voice_uz', 'reply-1')
            await live.fallback_speech('Salom', 'another_voice', 'reply-1')
            await live.fallback_speech('Salom', 'voice_uz', 'reply-2')
        with patch('core.live.httpx.AsyncClient', side_effect=lambda **kw: HTTPClient(transport=httpx.MockTransport(handle), **kw)):
            async_to_sync(run)()
        self.assertTrue(all(str(call.url) == 'https://api.voicelab.uz/v1/tts' for call in calls))
        self.assertEqual(json.loads(calls[0].content), {'text': 'Salom', 'language': 'uz', 'voice_id': 'voice_uz', 'speed': 1})
        keys = [call.headers['Idempotency-Key'] for call in calls]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(len(set(keys)), 3)
        self.assertNotIn('test-private-key', keys[0])

    def test_invalid_wav_and_fallback_credit_error_are_redacted(self):
        for response, code in [(httpx.Response(200, content=self.wav(16000), headers={'Content-Type': 'audio/wav'}), 'tts_protocol_error'),
                               (httpx.Response(200, content=self.wav()[:-4], headers={'Content-Type': 'audio/wav'}), 'tts_protocol_error'),
                               (httpx.Response(402, json={'error': {'code': 'test-private-key', 'message': 'test-private-key'}}), 'tts_insufficient_credits')]:
            with self.subTest(code=code), patch('core.live.httpx.AsyncClient', side_effect=lambda **kw: HTTPClient(transport=httpx.MockTransport(lambda req: response), **kw)):
                with self.assertRaises(voice.VoiceError) as error:
                    async_to_sync(live.fallback_speech)('Salom', 'voice_uz', 'reply-1')
                self.assertEqual(error.exception.code, code)
                self.assertNotIn('test-private-key', error.exception.message)

    def test_cancel_during_fallback_cancels_http_request(self):
        async def run():
            requested, cancelled = asyncio.Event(), asyncio.Event()
            async def handle(request):
                requested.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            with patch('core.live.PinnedConnection', return_value=FakeProvider(frames=[])), \
                    patch('core.live.voicelab.uzbek_voice', return_value='voice_uz'), \
                    patch('core.live.httpx.AsyncClient', side_effect=lambda **kw: HTTPClient(transport=httpx.MockTransport(handle), **kw)):
                stream = live.speech_stream('Salom')
                self.assertEqual(json.loads(await anext(stream))['type'], 'ready')
                self.assertEqual(json.loads(await anext(stream))['type'], 'recovering')
                pending = asyncio.create_task(anext(stream))
                await asyncio.wait_for(requested.wait(), 1)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError): await pending
                self.assertTrue(cancelled.is_set())
                await stream.aclose()
        async_to_sync(run)()


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   VOICELAB_API_KEY='test-private-key')
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
    def test_voicelab_key_is_never_sent_to_openai(self):
        self.assertFalse(voice.configured())
        with patch('core.voice.OpenAI') as provider:
            with self.assertRaises(voice.VoiceError) as error:
                with voice.provider(): pass
        self.assertEqual(error.exception.code, 'wrong_provider_key')
        provider.assert_not_called()

    def test_tts_stream_delivers_chunks_before_done_and_pins_destination(self):
        provider = FakeProvider()
        async def run():
            stream = live.speech_stream('Salom')
            ready = json.loads(await anext(stream))
            chunk = json.loads(await anext(stream))
            self.assertEqual(ready['type'], 'ready')
            self.assertEqual(chunk['type'], 'audio')
            self.assertFalse(provider.closed)
            self.assertEqual(json.loads(await anext(stream))['type'], 'done')
            await stream.aclose()
        with patch('core.live.PinnedConnection', return_value=provider) as connection, patch('core.live.voicelab.uzbek_voice', return_value='voice_uz'):
            async_to_sync(run)()
        self.assertEqual(connection.call_args.args[0], live.TTS_SOCKET)
        self.assertIsNone(connection.call_args.kwargs['origin'])
        self.assertTrue(provider.closed)
        self.assertEqual(json.loads(provider.sent[0])['language'], 'uz')

    def test_barge_in_closes_stream_and_interrupts_provider(self):
        provider = FakeProvider()
        async def run():
            stream = live.speech_stream('Salom')
            await anext(stream); await anext(stream)
            await stream.aclose()
        with patch('core.live.PinnedConnection', return_value=provider), patch('core.live.voicelab.uzbek_voice', return_value='voice_uz'):
            async_to_sync(run)()
        self.assertIn({'action': 'interrupt'}, [json.loads(x) for x in provider.sent])
        self.assertTrue(provider.closed)

    def test_provider_error_and_broken_pcm_are_redacted(self):
        for frames in [[json.dumps({'event': 'error', 'message': 'test-private-key'})], [b'odd']]:
            provider = FakeProvider(frames=frames)
            async def run(): return b''.join([chunk async for chunk in live.speech_stream('Salom')])
            with patch('core.live.PinnedConnection', return_value=provider), patch('core.live.voicelab.uzbek_voice', return_value='voice_uz'):
                data = async_to_sync(run)()
            self.assertNotIn(b'test-private-key', data)
            self.assertIn(b'"type": "error"', data)

    def test_socket_rejects_unauthenticated_frame_before_provider_call(self):
        async def run():
            app = ApplicationCommunicator(live.relay, self.scope())
            await app.send_input({'type': 'websocket.connect'})
            self.assertEqual((await app.receive_output())['type'], 'websocket.accept')
            await app.send_input({'type': 'websocket.receive', 'text': json.dumps({'ticket': 'forged'})})
            self.assertEqual((await app.receive_output())['code'], 4403)
            await app.wait()
        with patch('core.live.PinnedConnection') as provider: async_to_sync(run)()
        provider.assert_not_called()

    def test_socket_relays_pcm_and_returns_only_final_text(self):
        provider = FakeProvider(service='stt', frames=[json.dumps({'event': 'final', 'text': 'Topshiriqlarni och'})])
        async def run():
            app = ApplicationCommunicator(live.relay, self.scope())
            await app.send_input({'type': 'websocket.connect'}); await app.receive_output()
            await app.send_input({'type': 'websocket.receive', 'text': json.dumps({'ticket': 'signed'})})
            ready = json.loads((await app.receive_output())['text'])
            self.assertEqual(ready['sample_rate'], 16000)
            await app.send_input({'type': 'websocket.receive', 'text': json.dumps({'type': 'start', 'language': 'other'})})
            await app.send_input({'type': 'websocket.receive', 'bytes': b'\0\0'*3200})
            await app.send_input({'type': 'websocket.receive', 'text': '{"type":"commit"}'})
            self.assertEqual(json.loads((await app.receive_output())['text'])['event'], 'recognizing')
            final = json.loads((await app.receive_output())['text'])
            self.assertEqual(final, {'event': 'final', 'text': 'Topshiriqlarni och'})
            await app.wait()
        with patch('core.live.authenticate', return_value={'user_id': self.user.pk}), patch('core.live.provider_ticket', return_value=live.STT_SOCKET), patch('core.live.PinnedConnection', return_value=provider):
            async_to_sync(run)()
        self.assertEqual(json.loads(provider.sent[0])['language'], 'uz')
        self.assertEqual(provider.sent[1], b'\0\0'*3200)
        self.assertTrue(provider.closed)

    def test_unavailable_realtime_uses_one_rest_fallback_and_announces_progress(self):
        provider = FakeProvider(service='stt', frames=[json.dumps({'event': 'error', 'code': 'realtime_stt_unavailable', 'message': 'private-provider-data'})])
        provider.started = True
        sent = []
        async def send(data): sent.append(json.loads(data['text']))
        fallback = AsyncMock(return_value={'event': 'final', 'text': 'Topshiriqlarni och'})
        with patch('core.live.fallback_transcription', fallback):
            result = async_to_sync(live.recognize)(provider, b'\0\0'*16000, send)
        self.assertEqual(result['event'], 'final')
        self.assertEqual(sent, [{'event': 'processing', 'stage': 'recovering'}])
        fallback.assert_awaited_once()
        self.assertEqual(fallback.call_args.args[0], b'\0\0'*16000)
        self.assertNotIn('private-provider-data', json.dumps(sent))

    def test_credit_and_no_speech_errors_never_repeat_paid_stt(self):
        for code in ['insufficient_credits', 'no_speech_detected']:
            provider = FakeProvider(service='stt', frames=[json.dumps({'event': 'error', 'code': code})]); provider.started = True
            with patch('core.live.fallback_transcription', new_callable=AsyncMock) as fallback:
                result = async_to_sync(live.recognize)(provider, b'\0\0'*16000, AsyncMock())
            self.assertEqual(result, {'event': 'error', 'code': code})
            fallback.assert_not_awaited()

    def test_rest_fallback_uses_valid_wav_fixed_host_and_one_idempotency_key(self):
        seen = []
        def dispatch(request):
            seen.append(request)
            body = request.read()
            start = body.index(b'RIFF')
            with wave.open(io.BytesIO(body[start:]), 'rb') as audio:
                self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getsampwidth()), (16000, 1, 2))
                self.assertEqual(audio.readframes(16000), b'\1\0'*16000)
            return httpx.Response(200, json={'transcript': 'Topshiriqlarni och'})
        with patch('core.live.httpx.AsyncClient', side_effect=lambda **kwargs: HTTPClient(transport=httpx.MockTransport(dispatch), **kwargs)):
            result = async_to_sync(live.fallback_transcription)(b'\1\0'*16000, 'one-utterance')
        self.assertEqual(result, {'event': 'final', 'text': 'Topshiriqlarni och'})
        self.assertEqual(len(seen), 1)
        self.assertEqual(str(seen[0].url), 'https://api.voicelab.uz/v1/stt')
        self.assertEqual(seen[0].headers['Idempotency-Key'], 'one-utterance')

    def test_disconnect_cancels_pending_fallback(self):
        provider = FakeProvider(service='stt', frames=[json.dumps({'event': 'error', 'code': 'realtime_stt_unavailable'})])
        cancelled = []
        async def fallback(*args):
            try: await asyncio.Event().wait()
            finally: cancelled.append(True)
        async def run():
            app = ApplicationCommunicator(live.relay, self.scope())
            await app.send_input({'type': 'websocket.connect'}); await app.receive_output()
            await app.send_input({'type': 'websocket.receive', 'text': '{"ticket":"signed"}'}); await app.receive_output()
            await app.send_input({'type': 'websocket.receive', 'text': '{"type":"start"}'})
            await app.send_input({'type': 'websocket.receive', 'bytes': b'\0\0'*16000})
            await app.send_input({'type': 'websocket.receive', 'text': '{"type":"commit"}'})
            self.assertEqual(json.loads((await app.receive_output())['text'])['event'], 'recognizing')
            self.assertEqual(json.loads((await app.receive_output())['text'])['event'], 'processing')
            await app.send_input({'type': 'websocket.disconnect'}); await app.wait()
        with patch('core.live.authenticate', return_value={'user_id': self.user.pk}), patch('core.live.provider_ticket', return_value=live.STT_SOCKET), patch('core.live.PinnedConnection', return_value=provider), patch('core.live.fallback_transcription', fallback):
            async_to_sync(run)()
        self.assertTrue(cancelled)
        self.assertTrue(provider.closed)
