import uuid
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from . import speaker
from .models import VoiceProfile
from .voice_errors import VoiceError
from .voice_views import rate_limit, voice_access


def return_url(request):
    url = request.GET.get('next', '/')
    return url if url.startswith('/') and url_has_allowed_host_and_scheme(url, {request.get_host()}, require_https=request.is_secure()) else '/'


@login_required
@require_GET
def setup(request):
    profile = VoiceProfile.objects.filter(user=request.user).first()
    return render(request, 'core/voice_setup.html', {'page_title': 'Ovozimni tanitish',
        'profile': profile, 'return_url': return_url(request),
        'setup_action': reverse('voice_enroll') + '?' + urlencode({'next': return_url(request)})})


@voice_access
@require_POST
def enroll(request):
    rate_limit(request)
    if request.POST.get('consent') != '1' or set(request.FILES) != {'sample1', 'sample2', 'sample3'}:
        raise VoiceError('speaker_samples_required', 'Rozilikni belgilang va uchta ovoz namunasini yozing.', 400)
    if any(len(request.FILES.getlist(key)) != 1 for key in request.FILES):
        raise VoiceError('speaker_samples_required', 'Uchta alohida ovoz yozuvi kerak.', 400)
    recordings = [speaker.read_wav(request.FILES[f'sample{i}'], max_seconds=12) for i in range(1, 4)]
    vectors = speaker.enroll_vectors(recordings)
    with transaction.atomic():
        # Always bind to the authenticated account, never to a submitted user ID.
        VoiceProfile.objects.update_or_create(user=request.user, defaults={
            'encrypted_embedding': speaker.seal(request.user.pk, vectors), 'model_version': speaker.MODEL_SHA,
            'revision': uuid.uuid4(), 'consent_at': timezone.now()})
    return JsonResponse({'message': 'Ovozingiz tanitildi. Endi agent faqat ovozingiz mos kelgan buyruqlarni qabul qiladi.', 'next': return_url(request)})


@voice_access
@require_POST
def remove(request):
    rate_limit(request)
    VoiceProfile.objects.filter(user=request.user).delete()
    return JsonResponse({'message': 'Ovoz namunangiz o‘chirildi. Matnli agentdan foydalanishingiz mumkin.'})
