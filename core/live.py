"""Authenticated, same-origin ASGI media relay inside the Django monolith."""
import asyncio
import base64
import hashlib
import io
import json
import logging
import uuid
import wave
from http.cookies import CookieError, SimpleCookie
from importlib import import_module
from urllib.parse import urlencode

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user
from django.core import signing
from django.core.cache import cache
from django.http import HttpRequest
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException
import httpx

from . import voicelab
from .voice_errors import VoiceError

STT_SOCKET = 'wss://api.voicelab.uz/v1/stt/stream'
TTS_SOCKET = 'wss://api.voicelab.uz/v1/tts/stream'
logger = logging.getLogger(__name__)


def protocol_code(value):
    # Log protocol identifiers only, never audio, text or provider error bodies.
    return value if isinstance(value, str) and len(value) < 64 and value.replace('_', '').isalpha() else 'unknown'


class ClientDisconnected(Exception):
    pass


RECOVERABLE_STT = {'realtime_stt_unavailable', 'service_unavailable', 'overloaded', 'timeout', 'busy', 'connection_failed'}
STT_ERRORS = RECOVERABLE_STT | {'no_speech_detected', 'invalid_audio', 'audio_too_long', 'insufficient_credits', 'forbidden', 'rate_limit'}


def stt_error(code):
    return {'event': 'error', 'code': code if code in STT_ERRORS else 'recognition_failed'}


async def fallback_transcription(pcm, request_id):
    """One cancellable REST attempt on the same provider; no audio on disk."""
    if not 16000 <= len(pcm) <= 16000*2*30:
        return stt_error('invalid_audio')
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
        audio.writeframes(pcm)
    headers = {'Authorization': 'Bearer '+settings.VOICELAB_API_KEY, 'Idempotency-Key': request_id}
    try:
        async with asyncio.timeout(50), httpx.AsyncClient(timeout=httpx.Timeout(40, connect=10), follow_redirects=False) as client:
            response = voicelab.check_response(await client.post(voicelab.BASE_URL+'/v1/stt', headers=headers,
                files={'audio': ('live.wav', buffer.getvalue(), 'audio/wav')}, data={'language': 'uz', 'include_speakers': 'false'}))
            data = voicelab.read_json(response)
            if response.status_code == 202:
                from urllib.parse import quote
                job = data.get('id')
                if not isinstance(job, str) or not job or len(job) > 200:
                    raise ValueError('Invalid STT job')
                while True:
                    await asyncio.sleep(1)
                    response = voicelab.check_response(await client.get(voicelab.BASE_URL+'/v1/stt/transcriptions/'+quote(job, safe=''), headers=headers))
                    data = voicelab.read_json(response)
                    if data.get('status') not in ('queued', 'pending', 'processing', 'running'): break
            result = voicelab.transcription_result(data)
            if result['status'] != 'completed': raise ValueError('Incomplete STT')
            return {'event': 'final', 'text': result['transcript']}
    except VoiceError as error:
        if error.code in ('no_speech', 'voicelab_no_speech_detected'): return stt_error('no_speech_detected')
        if error.status == 402: return stt_error('insufficient_credits')
        if error.status == 429: return stt_error('rate_limit')
        if error.code == 'voicelab_overloaded': return stt_error('overloaded')
        return stt_error('service_unavailable')
    except (httpx.RequestError, ValueError, TimeoutError):
        return stt_error('service_unavailable')


async def recognize(upstream, pcm, send):
    try:
        result = json.loads(await asyncio.wait_for(upstream.recv(), 30))
    except (TimeoutError, WebSocketException, OSError):
        result = stt_error('timeout')
    if isinstance(result, dict) and result.get('event') == 'final' and isinstance(result.get('text'), str) and len(result['text']) <= 6000:
        return {'event': 'final', 'text': result['text']}
    code = result.get('code') if isinstance(result, dict) else None
    logger.warning('STT result code=%s', protocol_code(code))
    if code in RECOVERABLE_STT:
        await send({'type': 'websocket.send', 'text': json.dumps({'event': 'processing', 'stage': 'recovering'})})
        # A fresh UUID binds the single fallback attempt to this utterance.
        return await fallback_transcription(bytes(pcm), str(uuid.uuid4()))
    return stt_error(code)


class PinnedConnection(connect):
    def process_redirect(self, error):
        # Never forward credentials or a realtime ticket to a redirect target.
        return error


def make_ticket(request):
    return signing.dumps({'user': request.user.pk, 'session': hashlib.sha256(request.session.session_key.encode()).hexdigest(),
        'origin': request.scheme+'://'+request.get_host(), 'nonce': uuid.uuid4().hex}, salt='voice-live')


def authenticate(scope, token):
    if not isinstance(token, str) or len(token) > 4000:
        return False
    try:
        data = signing.loads(token, salt='voice-live', max_age=60)
        headers = dict(scope.get('headers', []))
        if headers.get(b'origin', b'').decode() != data['origin']:
            return False
        cookies = SimpleCookie(headers.get(b'cookie', b'').decode())
        key = cookies[settings.SESSION_COOKIE_NAME].value
        if hashlib.sha256(key.encode()).hexdigest() != data['session']:
            return False
        request = HttpRequest()
        request.session = import_module(settings.SESSION_ENGINE).SessionStore(session_key=key)
        user = get_user(request)
        if not (user.is_authenticated and user.is_active and user.pk == data['user']):
            return False
        if not cache.add('voice-live-used:'+data['nonce'], 1, timeout=65):
            return False
        return {'user_id': user.pk}
    except (signing.BadSignature, KeyError, ValueError, UnicodeError, CookieError, VoiceError):
        return False


async def provider_ticket():
    data = await sync_to_async(lambda: voicelab.read_json(voicelab.request('POST', '/v1/ticket',
        json={'transport': 'websocket', 'service': 'stt'})), thread_sensitive=False)()
    if data.get('websocket_url') != STT_SOCKET or data.get('service') != 'stt' or not isinstance(data.get('ticket'), str):
        raise ValueError('Invalid realtime destination')
    return STT_SOCKET + '?' + urlencode({'ticket': data['ticket']})


async def receive_frame(receive, timeout=40):
    item = await asyncio.wait_for(receive(), timeout=timeout)
    if item['type'] == 'websocket.disconnect':
        raise ClientDisconnected
    return item


async def verified_recognition(identity, pcm, send):
    await send({'type': 'websocket.send', 'text': json.dumps({'event': 'recognizing'})})
    url = await provider_ticket()
    async with PinnedConnection(url, origin=None, open_timeout=12, close_timeout=1, max_size=256000) as upstream:
        ready = json.loads(await asyncio.wait_for(upstream.recv(), 15))
        if not isinstance(ready, dict) or ready.get('event') != 'ready' or ready.get('sample_rate') != 16000 or ready.get('audio_format') != 'pcm_s16le' or ready.get('channels') != 1:
            raise ValueError('Invalid audio format')
        await upstream.send(json.dumps({'type': 'start', 'language': 'uz', 'audio_format': 'pcm_s16le', 'sample_rate': 16000, 'channels': 1}))
        for start in range(0, len(pcm), 16000):
            await upstream.send(bytes(pcm[start:start+16000]))
        await upstream.send(json.dumps({'type': 'commit'}))
        result = await recognize(upstream, pcm, send)
        return result


async def relay(scope, receive, send):
    if scope.get('path') != '/voice/live-stream/' or scope.get('query_string'):
        await send({'type': 'websocket.close', 'code': 4404}); return
    await receive()  # websocket.connect
    await send({'type': 'websocket.accept'})
    try:
        first = await receive_frame(receive, timeout=5)
        auth = json.loads(first.get('text', '')[:5000])
        identity = await sync_to_async(authenticate)(scope, auth.get('ticket')) if isinstance(auth, dict) else None
        if not identity:
            await send({'type': 'websocket.close', 'code': 4403}); return
        await send({'type': 'websocket.send', 'text': json.dumps({'event': 'ready', 'audio_format': 'pcm_s16le', 'sample_rate': 16000, 'channels': 1})})
        try:
            first = await receive_frame(receive, timeout=120)
        except TimeoutError:
            return
        if json.loads(first.get('text', '')).get('type') != 'start':
            raise ValueError('Start required')
        pcm = bytearray()
        async with asyncio.timeout(40):
            while True:
                item = await receive_frame(receive)
                chunk = item.get('bytes')
                if chunk is not None:
                    if len(chunk) % 2 or len(chunk) > 64000 or len(pcm)+len(chunk) > 16000*2*30:
                        raise ValueError('Audio limit')
                    pcm.extend(chunk)
                    continue
                message = json.loads(item.get('text', ''))
                if message.get('action') == 'interrupt':
                    return
                if message.get('type') != 'commit' or len(pcm) < 3200:
                    raise ValueError('Invalid audio commit')
                break
        # Listen for departure during verification AND provider connection. A
        # cancelled CPU calculation must never proceed to sending audio later.
        result_task = asyncio.create_task(verified_recognition(identity, pcm, send))
        client_task = asyncio.create_task(receive())
        try:
            complete, _ = await asyncio.wait([result_task, client_task], timeout=85, return_when=asyncio.FIRST_COMPLETED)
            if result_task in complete and client_task not in complete:
                await send({'type': 'websocket.send', 'text': json.dumps(result_task.result())})
        finally:
            result_task.cancel(); client_task.cancel()
            await asyncio.gather(result_task, client_task, return_exceptions=True)
    except ClientDisconnected:
        pass
    except (VoiceError, WebSocketException, OSError, ValueError, TypeError, KeyError, AttributeError, TimeoutError) as error:
        logger.warning('Realtime STT relay stopped: %s', type(error).__name__)
        try:
            await send({'type': 'websocket.send', 'text': json.dumps({'event': 'error', 'code': 'connection_failed'})})
        except OSError:
            pass
    finally:
        try:
            await send({'type': 'websocket.close', 'code': 1000})
        except (OSError, RuntimeError):
            pass


def event(value):
    return (json.dumps(value, ensure_ascii=False)+'\n').encode()


TTS_RECOVERABLE = {'busy', 'overloaded', 'timeout', 'service_unavailable', 'generation_failed',
                   'realtime_tts_unavailable', 'connection_failed', 'incomplete_stream'}


def tts_failure(code):
    messages = {
        'insufficient_credits': 'VoiceLab ovoz yaratish uchun kredit yetarli emasligini bildirdi.',
        'forbidden': 'VoiceLab kalitida kerakli ovoz yaratish ruxsati yo‘q.',
        'invalid_api_key': 'VoiceLab API kaliti yaroqsiz. Administrator kalitni tekshirsin.',
        'rate_limit': 'VoiceLab ovoz so‘rovlari limiti tugadi. Birozdan so‘ng «Tinglash»ni bosing.',
        'voice_unavailable': 'Tanlangan VoiceLab ovozi mavjud emas. Administrator ovoz sozlamasini tekshirsin.',
        'validation_error': 'VoiceLab ovoz yaratish parametrlarini qabul qilmadi.',
        'protocol_error': 'VoiceLab ovoz oqimi noto‘g‘ri formatda keldi. Javob matni saqlangan.',
        'interrupted': 'Ovozli javob to‘xtadi. Qayta eshitish uchun «Tinglash»ni bosing.',
    }
    aliases = {'insufficient_scope': 'forbidden', 'rate_limited': 'rate_limit', 'invalid_message': 'validation_error'}
    code = aliases.get(code, code)
    if code not in messages and code not in TTS_RECOVERABLE:
        code = 'unknown'
    message = messages.get(code, 'VoiceLab ovoz yaratishni yakunlay olmadi. Javob matni saqlangan; «Tinglash» orqali qayta urinishingiz mumkin.')
    return VoiceError('tts_'+code, message, 502)


def tts_event_error(message):
    if isinstance(message, dict) and message.get('event') in ('error', 'interrupted'):
        error = message.get('error')
        code = message.get('code') or (error.get('code') if isinstance(error, dict) else None)
        raise tts_failure('interrupted' if message['event'] == 'interrupted' else code)


async def tts_pcm_stream(text, voice):
    """Unstored realtime audio. Cancelling never initiates another synthesis."""
    try:
        async with PinnedConnection(TTS_SOCKET, additional_headers={'xvl-api-key': settings.VOICELAB_API_KEY},
                                    origin=None, open_timeout=8, close_timeout=1, max_size=4*1024*1024) as upstream:
            done = False
            try:
                ready = json.loads(await asyncio.wait_for(upstream.recv(), 8))
                tts_event_error(ready)
                if not isinstance(ready, dict) or ready.get('event') != 'ready' or ready.get('sample_rate') != 24000 or ready.get('channels') != 1 or ready.get('format') != 'pcm_s16le':
                    raise tts_failure('protocol_error')
                await upstream.send(json.dumps({'text': text, 'language': 'uz', 'voice_id': voice, 'speed': 1}))
                yield {'type': 'ready', 'sample_rate': 24000}
                total = 0
                iterator = upstream.__aiter__()
                while True:
                    try:
                        frame = await asyncio.wait_for(anext(iterator), 12 if not total else 15)
                    except StopAsyncIteration:
                        raise tts_failure('incomplete_stream')
                    if isinstance(frame, bytes):
                        if not frame:
                            continue
                        total += len(frame)
                        if len(frame) % 2 or total > 10*1024*1024:
                            raise tts_failure('protocol_error')
                        yield {'type': 'audio', 'data': base64.b64encode(frame).decode('ascii')}
                    else:
                        message = json.loads(frame)
                        tts_event_error(message)
                        if isinstance(message, dict) and message.get('event') == 'done':
                            if not total:
                                raise tts_failure('incomplete_stream')
                            done = True
                            yield {'type': 'done'}
                            return
            finally:
                if not done:
                    try:
                        await upstream.send(json.dumps({'action': 'interrupt'}))
                    except (WebSocketException, OSError):
                        pass
    except TimeoutError:
        raise tts_failure('timeout')
    except (WebSocketException, OSError) as error:
        status = getattr(getattr(error, 'response', None), 'status_code', None)
        raise tts_failure({401:'invalid_api_key', 402:'insufficient_credits', 403:'forbidden', 429:'rate_limit'}.get(status, 'connection_failed')) from error
    except (ValueError, TypeError, AttributeError):
        raise tts_failure('protocol_error')


async def fallback_speech(text, voice, request_id):
    """One cancellable REST generation, converted to the same browser PCM format."""
    body = {'text': text, 'language': 'uz', 'voice_id': voice, 'speed': 1}
    # Same signed reply and identical voice/text yield the same REST retry key.
    key = 'tts-fallback-' + hashlib.sha256((request_id + json.dumps(body, sort_keys=True)).encode()).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=8), follow_redirects=False) as client:
            response = await client.post(voicelab.BASE_URL+'/v1/tts',
                headers={'Authorization': 'Bearer '+settings.VOICELAB_API_KEY, 'Idempotency-Key': key}, json=body)
            try:
                voicelab.check_response(response)
            except VoiceError as error:
                code = {401: 'invalid_api_key', 402: 'insufficient_credits', 403: 'forbidden',
                        429: 'rate_limit', 503: 'service_unavailable'}.get(response.status_code,
                            error.code.removeprefix('voicelab_'))
                raise tts_failure(code) from error
        if response.headers.get('content-type', '').split(';')[0].strip() != 'audio/wav' or len(response.content) > 10*1024*1024:
            raise ValueError('Invalid WAV')
        with wave.open(io.BytesIO(response.content), 'rb') as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (24000, 1, 2, 'NONE'):
                raise ValueError('Invalid PCM')
            count = audio.getnframes()
            if not 0 < count <= 5*1024*1024:
                raise ValueError('Invalid length')
            pcm = audio.readframes(count)
            if len(pcm) != count*2:
                raise ValueError('Truncated WAV')
            return pcm
    except httpx.TimeoutException:
        raise tts_failure('timeout')
    except httpx.RequestError:
        raise tts_failure('connection_failed')
    except (wave.Error, EOFError, ValueError):
        raise tts_failure('protocol_error')


async def speech_stream(text, request_id=None):
    request_id = request_id or 'tts-'+uuid.uuid4().hex
    emitted_audio = False
    try:
        async with asyncio.timeout(55):
            if not settings.VOICELAB_API_KEY:
                raise VoiceError('voicelab_not_configured', 'VoiceLab API kaliti sozlanmagan.', 503)
            if not isinstance(text, str) or not text or len(text.encode('utf-8')) > 1000:
                raise VoiceError('speech_text', 'Ovozli javob matni 1000 UTF-8 baytdan oshmasligi kerak.')
            voice = await sync_to_async(voicelab.uzbek_voice, thread_sensitive=False)()
            stream = tts_pcm_stream(text, voice)
            try:
                async for item in stream:
                    if item['type'] == 'audio':
                        emitted_audio = True
                    yield event(item)
                return
            except VoiceError as error:
                logger.warning('TTS realtime code=%s audio_started=%s', error.code, emitted_audio)
                if emitted_audio or error.code.removeprefix('tts_') not in TTS_RECOVERABLE:
                    raise
            finally:
                await stream.aclose()
            # Only recover before playback begins, avoiding repeated spoken words.
            yield event({'type': 'recovering', 'message': 'Javob ovozi qayta tayyorlanmoqda…'})
            pcm = await fallback_speech(text, voice, request_id)
            yield event({'type': 'ready', 'sample_rate': 24000})
            for start in range(0, len(pcm), 24000):
                emitted_audio = True
                yield event({'type': 'audio', 'data': base64.b64encode(pcm[start:start+24000]).decode('ascii')})
            yield event({'type': 'done'})
    except VoiceError as error:
        logger.warning('TTS stopped code=%s audio_started=%s', error.code, emitted_audio)
        yield event({'type': 'error', 'code': error.code, 'message': error.message})
    except TimeoutError:
        error = tts_failure('timeout')
        logger.warning('TTS stopped code=tts_timeout audio_started=%s', emitted_audio)
        yield event({'type': 'error', 'code': error.code, 'message': error.message})
