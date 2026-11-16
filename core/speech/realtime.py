from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_POST

from .live import make_ticket, speech_stream
from .voice_views import json_payload, rate_limit, read_ticket, voice_access


@voice_access
@require_POST
def session(request):
    rate_limit(request)
    return JsonResponse({'ticket': make_ticket(request), 'websocket_path': '/voice/live-stream/'})


@voice_access
@require_POST
def speak(request):
    rate_limit(request)
    data = read_ticket(json_payload(request).get('reply_token'), 'voice-tts', request.user.pk)
    # Only this user's server-issued reply may be synthesized. No arbitrary
    # text or permanent provider credential is accepted from the browser.
    return StreamingHttpResponse(speech_stream(data['text'], request_id=data['id']), content_type='application/x-ndjson',
                                 headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-store'})
