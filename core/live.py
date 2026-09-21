"""Authenticated, same-origin ASGI media relay inside the Django monolith."""
import asyncio
import base64
import hashlib
import json
import logging
import uuid
from http.cookies import CookieError, SimpleCookie
from importlib import import_module

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user
from django.core import signing
from django.core.cache import cache
from django.http import HttpRequest
import httpx

from . import muxlisa
from .voice_errors import VoiceError

logger = logging.getLogger(__name__)


def protocol_code(value):
    # Log protocol identifiers only, never audio, text or provider error bodies.
    return value if isinstance(value, str) and len(value) < 64 and value.replace('_', '').isalpha() else 'unknown'


class ClientDisconnected(Exception):
    pass


STT_ERRORS = {'service_unavailable', 'timeout', 'no_speech_detected', 'invalid_audio',
              'audio_too_long', 'insufficient_credits', 'forbidden', 'rate_limit'}


def stt_error(code):
    return {'event': 'error', 'code': code if code in STT_ERRORS else 'recognition_failed'}


def stt_error_for(error):
    codes = {'no_speech': 'no_speech_detected', 'muxlisa_insufficient_credits': 'insufficient_credits',
             'muxlisa_rate_limit': 'rate_limit', 'muxlisa_forbidden': 'forbidden',
             'muxlisa_invalid_api_key': 'forbidden', 'muxlisa_invalid_audio': 'invalid_audio',
             'muxlisa_audio_size': 'audio_too_long', 'muxlisa_timeout': 'timeout'}
    return stt_error(codes.get(error.code, 'service_unavailable'))


async def transcribe_utterance(pcm):
    """One cancellable REST call per utterance; no audio on disk, no auto retry."""
    if not 16000 <= len(pcm) <= 16000*2*muxlisa.MAX_STT_SECONDS:
        return stt_error('invalid_audio')
    try:
        async with asyncio.timeout(50), httpx.AsyncClient(timeout=httpx.Timeout(40, connect=10), follow_redirects=False) as client:
            response = muxlisa.check_response(await client.post(
                muxlisa.BASE_URL + muxlisa.STT_PATH, headers=muxlisa.credentials(),
                files={'audio': ('live.wav', muxlisa.wav_file(bytes(pcm)), 'audio/wav')}))
            return {'event': 'final', 'text': muxlisa.transcription_result(muxlisa.read_json(response))['transcript']}
    except VoiceError as error:
        logger.warning('STT result code=%s', protocol_code(error.code))
        return stt_error_for(error)
    except (httpx.RequestError, ValueError, TimeoutError):
        logger.warning('STT result code=%s', 'service_unavailable')
        return stt_error('service_unavailable')


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


async def receive_frame(receive, timeout=40):
    item = await asyncio.wait_for(receive(), timeout=timeout)
    if item['type'] == 'websocket.disconnect':
        raise ClientDisconnected
    return item


async def verified_recognition(identity, pcm, send):
    await send({'type': 'websocket.send', 'text': json.dumps({'event': 'recognizing'})})
    return await transcribe_utterance(pcm)


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
    except (VoiceError, OSError, ValueError, TypeError, KeyError, AttributeError, TimeoutError) as error:
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


def tts_failure(code):
    messages = {
        'insufficient_credits': 'Muxlisa hisobida ovoz yaratish uchun mablag‘ yetarli emas.',
        'forbidden': 'Muxlisa kalitida kerakli ovoz yaratish ruxsati yo‘q.',
        'invalid_api_key': 'Muxlisa API kaliti yaroqsiz. Administrator kalitni tekshirsin.',
        'rate_limit': 'Muxlisa ovoz so‘rovlari limiti tugadi. Birozdan so‘ng «Tinglash»ni bosing.',
        'validation_error': 'Muxlisa ovoz yaratish parametrlarini qabul qilmadi.',
        'protocol_error': 'Muxlisa ovoz fayli noto‘g‘ri formatda keldi. Javob matni saqlangan.',
        'timeout': 'Muxlisa ovozli javobni tayyorlashga ulgurmadi. «Tinglash» orqali qayta urinib ko‘ring.',
        'connection_failed': 'Server Muxlisa bilan bog‘lana olmadi. Javob matni saqlangan.',
    }
    message = messages.get(code, 'Muxlisa ovoz yaratishni yakunlay olmadi. Javob matni saqlangan; «Tinglash» orqali qayta urinishingiz mumkin.')
    return VoiceError('tts_'+(code if code in messages else 'unknown'), message, 502)


async def synthesize_speech(text):
    """One cancellable REST generation; the provider has no realtime audio socket."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=8), follow_redirects=False) as client:
            response = await client.post(muxlisa.BASE_URL + muxlisa.TTS_PATH,
                                         headers=muxlisa.credentials(), json=muxlisa.speech_body(text))
            try:
                muxlisa.check_response(response)
            except VoiceError as error:
                codes = {401: 'invalid_api_key', 402: 'insufficient_credits', 403: 'forbidden',
                         422: 'validation_error', 429: 'rate_limit', 400: 'validation_error'}
                raise tts_failure(codes.get(response.status_code, 'connection_failed')) from error
            muxlisa.check_wav(response.content, response.headers.get('content-type', ''))
            return muxlisa.pcm_from_wav(response.content)
    except httpx.TimeoutException as error:
        raise tts_failure('timeout') from error
    except httpx.RequestError as error:
        raise tts_failure('connection_failed') from error
    except VoiceError as error:
        raise error if error.code.startswith('tts_') else tts_failure('protocol_error') from error


async def speech_stream(text, request_id=None):
    emitted_audio = False
    try:
        async with asyncio.timeout(55):
            if not settings.MUXLISA_API_KEY:
                raise VoiceError('muxlisa_not_configured', 'Muxlisa API kaliti sozlanmagan.', 503)
            if not isinstance(text, str) or not text or len(text) > muxlisa.MAX_TTS_CHARACTERS:
                raise VoiceError('speech_text', f'Ovozli javob matni {muxlisa.MAX_TTS_CHARACTERS} belgidan oshmasligi kerak.')
            pcm, rate = await synthesize_speech(text)
            yield event({'type': 'ready', 'sample_rate': rate})
            # Chunk by one second so playback can start before the last frame is read.
            for start in range(0, len(pcm), rate * 2):
                emitted_audio = True
                yield event({'type': 'audio', 'data': base64.b64encode(pcm[start:start + rate * 2]).decode('ascii')})
            yield event({'type': 'done'})
    except VoiceError as error:
        logger.warning('TTS stopped code=%s audio_started=%s', error.code, emitted_audio)
        yield event({'type': 'error', 'code': error.code, 'message': error.message})
    except TimeoutError:
        error = tts_failure('timeout')
        logger.warning('TTS stopped code=tts_timeout audio_started=%s', emitted_audio)
        yield event({'type': 'error', 'code': error.code, 'message': error.message})
