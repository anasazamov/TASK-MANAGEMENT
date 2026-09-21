"""Muxlisa AI STT/TTS transport. Never uses website sessions or OpenAI audio APIs.

The provider exposes synchronous REST only (no realtime sockets) and has no
idempotency keys, so a repeated request is a repeated charge: nothing here
retries automatically.
"""
import io
import wave
from pathlib import Path

import httpx
from django.conf import settings

from .voice_errors import VoiceError

BASE_URL = 'https://service.muxlisa.uz'
STT_PATH = '/api/v2/stt'
TTS_PATH = '/api/v2/tts'
MAX_STT_BYTES = 5 * 1024 * 1024
MAX_STT_SECONDS = 60
MAX_TTS_CHARACTERS = 1000
AUDIO_TYPES = {'.webm': 'audio/webm', '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4a': 'audio/x-m4a',
               '.aac': 'audio/aac', '.ogg': 'audio/ogg', '.flac': 'audio/flac', '.mp4': 'audio/mp4'}


def credentials():
    if not settings.MUXLISA_API_KEY:
        raise VoiceError('muxlisa_not_configured', 'Muxlisa API kaliti sozlanmagan. Administratorga murojaat qiling.', 503)
    return {'x-api-key': settings.MUXLISA_API_KEY}


def request(method, path, **kwargs):
    try:
        with httpx.Client(timeout=httpx.Timeout(45, connect=10), follow_redirects=False) as client:
            response = client.request(method, BASE_URL + path, headers=credentials(), **kwargs)
    except httpx.TimeoutException as error:
        raise VoiceError('muxlisa_timeout', 'Muxlisa javobi kechikdi. Qayta urinish tugmasidan foydalaning.', 504) from error
    except httpx.RequestError as error:
        raise VoiceError('muxlisa_connection', 'Server Muxlisa bilan bog‘lana olmadi. Qayta urinib ko‘ring.', 502) from error
    return check_response(response)


def check_response(response):
    """Map provider statuses to our own wording; provider bodies are never shown."""
    if 200 <= response.status_code < 300:
        return response
    messages = {
        400: ('muxlisa_invalid_audio', 'Muxlisa audio yoki matnni qabul qilmadi. Yozuvni qayta yuboring.', 400),
        401: ('muxlisa_invalid_api_key', 'Muxlisa API kaliti yaroqsiz yoki o‘chirilgan.', 502),
        403: ('muxlisa_forbidden', 'Muxlisa kalitida kerakli STT yoki TTS ruxsati yo‘q.', 502),
        402: ('muxlisa_insufficient_credits', 'Muxlisa hisobida mablag‘ yetarli emas. Balansni to‘ldiring.', 402),
        413: ('muxlisa_audio_size', 'Audio hajmi 5 MB dan oshmasligi kerak.', 413),
        422: ('muxlisa_validation', 'Muxlisa so‘rov parametrlarini qabul qilmadi.', 422),
        429: ('muxlisa_rate_limit', 'Muxlisa so‘rovlar limiti tugadi. Birozdan so‘ng qayta urinib ko‘ring.', 429),
    }
    code, message, status = messages.get(response.status_code,
        ('muxlisa_unavailable', 'Muxlisa xizmati vaqtincha javob bermayapti. Keyinroq qayta urinib ko‘ring.', 502))
    raise VoiceError(code, message, status)


def read_json(response):
    try:
        data = response.json()
    except ValueError as error:
        raise VoiceError('muxlisa_response', 'Muxlisa noto‘g‘ri javob qaytardi.', 502) from error
    if not isinstance(data, dict):
        raise VoiceError('muxlisa_response', 'Muxlisa javobi kutilgan formatda emas.', 502)
    return data


def transcription_result(data):
    transcript = data.get('text')
    if not isinstance(transcript, str):
        raise VoiceError('muxlisa_response', 'Muxlisa transkripsiya matnini qaytarmadi.', 502)
    if not transcript.strip():
        raise VoiceError('no_speech', 'Nutq eshitilmadi. Qayta yozib ko‘ring.')
    if len(transcript) > 6000:
        raise VoiceError('transcript_too_long', 'Buyruq juda uzun. Qisqaroq audio yozing.')
    return {'status': 'completed', 'transcript': transcript.strip()}


def transcribe(upload):
    extension = Path(upload.name).suffix.lower()
    if extension not in AUDIO_TYPES:
        raise VoiceError('audio_format', 'WebM, WAV, MP3, M4A, AAC, OGG yoki FLAC audio yuboring.')
    if not upload.size or upload.size > min(MAX_STT_BYTES, settings.VOICE_MAX_AUDIO_BYTES):
        raise VoiceError('audio_size', 'Audio bo‘sh yoki hajmi 5 MB dan katta.', 413)
    response = request('POST', STT_PATH,
                       files={'audio': ('command' + extension, upload.read(), AUDIO_TYPES[extension])})
    return transcription_result(read_json(response))


def wav_file(pcm, sample_rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(pcm)
    return buffer.getvalue()


def speech_excerpt(text):
    if len(text) <= MAX_TTS_CHARACTERS:
        return text
    ending = '. Davomini ekrandan o‘qing.'
    start = text[:MAX_TTS_CHARACTERS - len(ending)]
    return start.rsplit(' ', 1)[0].rstrip('.,;:') + ending if ' ' in start else start + ending


def speech_body(text):
    if not text or len(text) > MAX_TTS_CHARACTERS:
        raise VoiceError('speech_text', f'Ovozli javob matni {MAX_TTS_CHARACTERS} belgidan oshmasligi kerak.')
    return {'text': text, 'speaker': settings.MUXLISA_SPEAKER}


def check_wav(data, content_type):
    if (content_type.split(';')[0].strip() not in ('audio/wav', 'audio/x-wav', 'audio/wave')
            or not data.startswith(b'RIFF') or data[8:12] != b'WAVE' or len(data) > 10 * 1024 * 1024):
        raise VoiceError('tts_response', 'Muxlisa yaroqli WAV javobini qaytarmadi.', 502)
    return data


def synthesize(text):
    response = request('POST', TTS_PATH, json=speech_body(text))
    return check_wav(response.content, response.headers.get('content-type', ''))


def pcm_from_wav(data):
    """Browser playback needs raw mono PCM16 plus the provider's own sample rate."""
    try:
        with wave.open(io.BytesIO(data), 'rb') as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (1, 2, 'NONE'):
                raise ValueError('Invalid PCM')
            rate = audio.getframerate()
            if not 8000 <= rate <= 48000:
                raise ValueError('Invalid sample rate')
            count = audio.getnframes()
            if not 0 < count <= 5 * 1024 * 1024:
                raise ValueError('Invalid length')
            pcm = audio.readframes(count)
            if len(pcm) != count * 2:
                raise ValueError('Truncated WAV')
            return pcm, rate
    except (wave.Error, EOFError, ValueError) as error:
        raise VoiceError('tts_response', 'Muxlisa ovoz fayli noto‘g‘ri formatda keldi.', 502) from error
