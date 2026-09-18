"""VoiceLab STT/TTS transport. Never uses website sessions or OpenAI audio APIs."""
import hashlib
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
from django.conf import settings
from django.core.cache import cache

from .voice_errors import VoiceError

BASE_URL = 'https://api.voicelab.uz'


def request(method, path, **kwargs):
    if not settings.VOICELAB_API_KEY:
        raise VoiceError('voicelab_not_configured', 'VoiceLab API kaliti sozlanmagan. Administratorga murojaat qiling.', 503)
    headers = {'Authorization': f'Bearer {settings.VOICELAB_API_KEY}', **kwargs.pop('headers', {})}
    try:
        with httpx.Client(timeout=httpx.Timeout(45, connect=10), follow_redirects=False) as client:
            response = client.request(method, BASE_URL + path, headers=headers, **kwargs)
    except httpx.TimeoutException as error:
        raise VoiceError('voicelab_timeout', 'VoiceLab javobi kechikdi. Qayta urinish tugmasidan foydalaning.', 504) from error
    except httpx.RequestError as error:
        raise VoiceError('voicelab_connection', 'Server VoiceLab bilan bog‘lana olmadi. Qayta urinib ko‘ring.', 502) from error
    return check_response(response)


def check_response(response):
    if 200 <= response.status_code < 300:
        return response
    try:
        body = response.json()
        code = body.get('error', {}).get('code', '') if isinstance(body, dict) else ''
    except (ValueError, AttributeError):
        code = ''
    if not isinstance(code, str):
        code = ''
    messages = {
        'no_speech_detected': 'Ovozda nutq topilmadi. Mikrofonga yaqinroq gapiring.',
        'invalid_audio': 'Audio buzilgan yoki juda qisqa. Kamida yarim soniya gapirib qayta yozing.',
        'unsupported_audio_format': 'Audio formati qo‘llanmaydi. WebM, WAV, MP3, M4A, OGG yoki FLAC yuboring.',
        'unsupported_language': 'VoiceLab hisobida o‘zbek tili hozir mavjud emas.',
        'long_stt_unavailable': 'Uzun audio xizmati mavjud emas. 30 soniyadan qisqa audio yozing.',
        'idempotency_key_reused': 'Bu qayta urinish identifikatori boshqa yozuvga tegishli. Yangi audio yozing.',
        'voice_unavailable': 'Tanlangan ovoz mavjud emas. Administrator TTS ovozini tekshirsin.',
    }
    defaults = {
        401: 'VoiceLab API kaliti yaroqsiz yoki o‘chirilgan.',
        402: 'VoiceLab krediti yetarli emas yoki kalit limiti tugagan.',
        403: 'VoiceLab kalitida kerakli STT, TTS yoki ovoz katalogi ruxsati yo‘q.',
        413: 'Audio hajmi 10 MB dan oshmasligi kerak.',
        422: 'VoiceLab audio yoki ovoz sozlamalarini qabul qilmadi.',
        429: 'VoiceLab so‘rovlar limiti tugadi. Birozdan so‘ng qayta urinib ko‘ring.',
        503: 'VoiceLab xizmati vaqtincha mavjud emas. Keyinroq qayta urinib ko‘ring.',
    }
    status = response.status_code if response.status_code in (402, 413, 422, 429) else 502
    raise VoiceError('voicelab_' + (code or 'error'), messages.get(code, defaults.get(response.status_code,
                     'VoiceLab so‘rovni bajara olmadi. Administrator xizmat sozlamalarini tekshirsin.')), status)


def read_json(response):
    try:
        data = response.json()
    except ValueError as error:
        raise VoiceError('voicelab_response', 'VoiceLab noto‘g‘ri javob qaytardi.', 502) from error
    if not isinstance(data, dict):
        raise VoiceError('voicelab_response', 'VoiceLab javobi kutilgan formatda emas.', 502)
    return data


def transcription_result(data):
    status = data.get('status')
    if status in ('queued', 'pending', 'processing', 'running'):
        return {'status': 'processing'}
    if status in ('failed', 'cancelled'):
        error = data.get('error')
        code = error.get('code') if isinstance(error, dict) else None
        if code in ('stt_overloaded', 'overloaded'):
            raise VoiceError('voicelab_overloaded', 'VoiceLab nutqni tanish xizmati band. Birozdan so‘ng yozuvni qayta yuboring yoki buyruqni matn bilan kiriting.', 503)
        if code in ('stt_unavailable', 'realtime_stt_unavailable', 'service_unavailable'):
            raise VoiceError('voicelab_unavailable', 'VoiceLab nutqni tanish xizmati vaqtincha ishlamayapti. Birozdan so‘ng yozuvni qayta yuboring.', 503)
        if code == 'no_speech_detected':
            raise VoiceError('no_speech', 'Yozuvda nutq topilmadi. Mikrofonga yaqinroq gapirib qayta yozing.')
        if code == 'insufficient_credits':
            raise VoiceError('voicelab_insufficient_credits', 'VoiceLab krediti yetarli emas yoki kalit limiti tugagan.', 402)
        raise VoiceError('transcription_failed', 'VoiceLab audio tahlilini yakunlay olmadi. Birozdan so‘ng qayta yozing yoki buyruqni matn bilan kiriting.', 502)
    transcript = data.get('transcript')
    if not isinstance(transcript, str):
        raise VoiceError('voicelab_response', 'VoiceLab transkripsiya matnini qaytarmadi.', 502)
    if not transcript.strip():
        raise VoiceError('no_speech', 'Nutq eshitilmadi. Qayta yozib ko‘ring.')
    if len(transcript) > 6000:
        raise VoiceError('transcript_too_long', 'Buyruq juda uzun. Qisqaroq audio yozing.')
    return {'status': 'completed', 'transcript': transcript.strip()}


def transcribe(upload, user_id, request_id):
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise VoiceError('request_id_invalid', 'Audio so‘rovining identifikatori noto‘g‘ri.') from error
    extension = Path(upload.name).suffix.lower()
    if extension not in {'.webm', '.wav', '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.flac'}:
        raise VoiceError('audio_format', 'WebM, WAV, MP3, M4A, AAC, OGG yoki FLAC audio yuboring.')
    if not upload.size or upload.size > settings.VOICE_MAX_AUDIO_BYTES:
        raise VoiceError('audio_size', 'Audio bo‘sh yoki hajmi 10 MB dan katta.', 413)
    # Bind a retry to its user. The browser reuses request_id for the same audio.
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f'topshiriqlar:stt:{user_id}:{request_id}'))
    response = request('POST', '/v1/stt', headers={'Idempotency-Key': key},
                       files={'audio': ('command' + extension, upload.read(), upload.content_type or 'application/octet-stream')},
                       data={'language': 'uz', 'include_speakers': 'false'})
    data = read_json(response)
    if response.status_code == 202:
        job_id = data.get('id')
        if not isinstance(job_id, str) or not job_id or len(job_id) > 200:
            raise VoiceError('voicelab_response', 'VoiceLab audio identifikatorini qaytarmadi.', 502)
        return {'status': 'processing', 'id': job_id}
    return transcription_result(data)


def transcription_status(job_id):
    return transcription_result(read_json(request('GET', '/v1/stt/transcriptions/' + quote(job_id, safe=''))))


def uzbek_voice():
    # Cache is scoped to the credential and configured voice, preventing stale
    # account-owned voice selection after a key change. No raw keys in cache IDs.
    digest = hashlib.sha256((settings.VOICELAB_API_KEY + '|' + settings.VOICELAB_VOICE_ID).encode()).hexdigest()
    cache_key = 'voicelab:voice:' + digest
    selected = cache.get(cache_key)
    if selected:
        return selected
    languages = read_json(request('GET', '/v1/tts/languages')).get('data', [])
    if not isinstance(languages, list) or not any(isinstance(x, dict) and x.get('code') == 'uz' for x in languages):
        raise VoiceError('tts_language', 'VoiceLab TTS katalogida o‘zbek tili mavjud emas.', 503)
    voices = read_json(request('GET', '/v1/voices', params={'language': 'uz'})).get('data', [])
    candidates = [v for v in voices if isinstance(v, dict) and v.get('language') == 'uz' and isinstance(v.get('id'), str) and v['id']] if isinstance(voices, list) else []
    if settings.VOICELAB_VOICE_ID:
        candidates = [v for v in candidates if v['id'] == settings.VOICELAB_VOICE_ID]
    else:
        candidates.sort(key=lambda v: v.get('kind') != 'system')
    if not candidates:
        raise VoiceError('tts_voice', 'Mos o‘zbekcha ovoz topilmadi. Administrator ovoz sozlamasini tekshirsin.', 503)
    selected = candidates[0]['id']
    cache.set(cache_key, selected, timeout=300)
    return selected


def speech_excerpt(text):
    if len(text.encode('utf-8')) <= 1000:
        return text
    ending = '. Davomini ekrandan o‘qing.'
    start = text.encode('utf-8')[:1000 - len(ending.encode('utf-8'))].decode('utf-8', errors='ignore')
    return start.rsplit(' ', 1)[0].rstrip('.,;:') + ending if ' ' in start else start + ending


def synthesize(text, request_id):
    if not text or len(text.encode('utf-8')) > 1000:
        raise VoiceError('speech_text', 'Ovozli javob matni 1000 UTF-8 baytdan oshmasligi kerak.')
    response = request('POST', '/v1/tts', headers={'Idempotency-Key': request_id},
                       json={'text': text, 'language': 'uz', 'voice_id': uzbek_voice(), 'speed': 1})
    audio = response.content
    if (response.headers.get('content-type', '').split(';')[0].strip() != 'audio/wav'
            or not audio.startswith(b'RIFF') or audio[8:12] != b'WAVE' or len(audio) > 10 * 1024 * 1024):
        raise VoiceError('tts_response', 'VoiceLab yaroqli WAV javobini qaytarmadi.', 502)
    return audio
