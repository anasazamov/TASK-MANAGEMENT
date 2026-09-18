import json
import time
import uuid
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.core import signing
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import voice, voicelab
from .models import Task
from .permissions import can_delegate
from .speech_text import spoken_text


def voice_access(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'auth_required', 'message': 'Sessiya tugagan. Qayta tizimga kiring.'}, status=401)
        if not request.user.is_active:
            return JsonResponse({'error': 'forbidden', 'message': 'Hisobingiz faol emas.'}, status=403)
        try:
            return view(request, *args, **kwargs)
        except voice.VoiceError as error:
            # Never return provider exception bodies, credentials or transcripts.
            return JsonResponse({'error': error.code, 'message': error.message}, status=error.status)
    return wrapped


def rate_limit(request):
    # The default monolith runs one process. Use a shared Django cache when
    # deploying multiple processes to enforce this limit across all workers.
    key = f'voice:{request.user.pk}:{int(time.time() // 60)}'
    if cache.add(key, 1, timeout=70):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=70)
            count = 1
    if count > settings.VOICE_REQUESTS_PER_MINUTE:
        raise voice.VoiceError('rate_limit', 'Juda ko‘p so‘rov yuborildi. Bir daqiqadan so‘ng qayta urinib ko‘ring.', 429)


@voice_access
@require_GET
def status(request):
    llm, speech = voice.configured(), bool(settings.VOICELAB_API_KEY)
    return JsonResponse({'configured': llm and speech, 'stt': speech, 'llm': llm, 'tts': speech,
                         'max_audio_bytes': settings.VOICE_MAX_AUDIO_BYTES, 'max_seconds': settings.VOICE_MAX_SECONDS})


def json_payload(request):
    if request.content_type != 'application/json':
        raise voice.VoiceError('json_required', 'JSON formatidagi buyruq yuboring.', 415)
    if len(request.body) > 40000:
        raise voice.VoiceError('body_too_large', 'Buyruq juda katta.', 413)
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise voice.VoiceError('invalid_json', 'Buyruq formati noto‘g‘ri.')
    if not isinstance(payload, dict):
        raise voice.VoiceError('invalid_json', 'Buyruq formati noto‘g‘ri.')
    return payload


def read_ticket(value, salt, user_id):
    if not isinstance(value, str) or len(value) > 8000:
        raise voice.VoiceError('invalid_ticket', 'Ovozli so‘rov identifikatori noto‘g‘ri.')
    try:
        data = signing.loads(value, salt=salt, max_age=900)
    except signing.BadSignature:
        raise voice.VoiceError('expired_ticket', 'Ovozli javob yoki yozuv eskirgan. Buyruqni qayta kiriting.', 400)
    if data.get('user') != user_id:
        raise voice.VoiceError('ticket_forbidden', 'Bu ovozli so‘rov sizga tegishli emas.', 403)
    return data


@voice_access
@require_POST
def transcribe(request):
    rate_limit(request)
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        raise voice.VoiceError('invalid_length', 'Audio so‘rovi noto‘g‘ri.')
    if length > settings.VOICE_MAX_AUDIO_BYTES + 65536:
        raise voice.VoiceError('audio_too_large', 'Audio hajmi 10 MB dan oshmasligi kerak.', 413)
    upload = request.FILES.get('audio')
    if not upload or len(request.FILES) != 1:
        raise voice.VoiceError('audio_required', 'Bitta audio yozuv yuboring.')
    upload.seek(0)
    result = voicelab.transcribe(upload, request.user.pk, request.POST.get('request_id'))
    if result['status'] == 'processing':
        ticket = signing.dumps({'user': request.user.pk, 'id': result['id']}, salt='voice-stt')
        return JsonResponse({'status': 'processing', 'ticket': ticket}, status=202)
    return JsonResponse(result)


@voice_access
@require_POST
def transcription_status(request):
    # No new generation or charge: poll only this user's signed job reference.
    data = read_ticket(json_payload(request).get('ticket'), 'voice-stt', request.user.pk)
    return JsonResponse(voicelab.transcription_status(data['id']))


@voice_access
@require_POST
def draft(request):
    if not request.user.can_assign:
        raise voice.VoiceError('forbidden', 'Topshiriq berish huquqingiz yo‘q.', 403)
    rate_limit(request)
    payload = json_payload(request)
    parent = None
    parent_id = payload.get('parent_id')
    if parent_id is not None:
        if type(parent_id) is not int:
            raise voice.VoiceError('parent_invalid', 'Asosiy topshiriq noto‘g‘ri.')
        parent = get_object_or_404(Task.objects.visible_to(request.user), pk=parent_id)
        if not can_delegate(request.user, parent):
            raise voice.VoiceError('parent_forbidden', 'Bu topshiriqni taqsimlay olmaysiz.', 403)
    result = voice.draft_task(request.user, payload.get('command'), payload.get('current', {}), parent)
    spoken = spoken_text(result['message'])
    speech = voicelab.speech_excerpt(spoken)
    result['speech_shortened'] = speech != spoken
    # TTS may speak only a server-issued reply for the authenticated user.
    result['reply_token'] = signing.dumps({'user': request.user.pk, 'text': speech, 'id': 'tts-' + uuid.uuid4().hex}, salt='voice-tts')
    return JsonResponse(result)


@voice_access
@require_POST
def speak(request):
    rate_limit(request)
    data = read_ticket(json_payload(request).get('reply_token'), 'voice-tts', request.user.pk)
    audio = voicelab.synthesize(data['text'], data['id'])
    return HttpResponse(audio, content_type='audio/wav', headers={'Content-Disposition': 'inline; filename="agent-reply.wav"'})
