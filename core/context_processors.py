from django.conf import settings
from django.utils import timezone
from .models import ExcuseRequest, Task


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
        # Excuse requests waiting for the secretary's decision: the Davomat tab badge.
        # Leadership exempt from attendance has no "Mening davomatim" to look at.
        'attendance_tracked': request.user.role not in settings.ATTENDANCE_EXEMPT_ROLES,
        'nav_excuse_pending': (ExcuseRequest.objects.filter(state=ExcuseRequest.State.PENDING).count()
                               if request.user.can_manage_attendance else 0),
    }
