from django.conf import settings
from django.utils import timezone
from .models import Task


def navigation(request):
    if not request.user.is_authenticated:
        return {}
    tasks = Task.objects.visible_to(request.user)
    return {
        'nav_active_count': tasks.exclude(status='accepted').count(),
        'unread_count': request.user.notifications.filter(read_at__isnull=True, task__in=tasks).count(),
        'today': timezone.localdate(),
        'current_page': request.resolver_match.url_name if request.resolver_match else '',
        'push_key': settings.VAPID_PUBLIC_KEY,
    }
