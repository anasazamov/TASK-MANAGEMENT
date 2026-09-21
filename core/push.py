"""Browser push notifications: the site may be closed, the browser running.

Delivery is best effort. A push carries the notification title and the task code,
never task content, and a dead endpoint is deleted rather than retried forever.
"""
import json
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .models import PushSubscription

logger = logging.getLogger(__name__)


def configured():
    return bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY)


STALE_DAYS = 30


def send(notification):
    """Queue one delivery per subscribed browser of this user, after the commit."""
    if not configured():
        return
    payload = json.dumps({'title': notification.title,
                          'body': f'{notification.task.code} — {notification.task.title}'[:180],
                          'url': notification.task.get_absolute_url()}, ensure_ascii=False)
    subscriptions = list(PushSubscription.objects.filter(user_id=notification.user_id))
    if subscriptions:
        transaction.on_commit(lambda: background(subscriptions, payload))


def background(subscriptions, payload):
    """A slow or unreachable push service must never hold up the page request."""
    threading.Thread(target=deliver, args=(subscriptions, payload), daemon=True).start()


def deliver(subscriptions, payload):
    from pywebpush import WebPushException, webpush
    for subscription in subscriptions:
        try:
            webpush(subscription_info={'endpoint': subscription.endpoint,
                                       'keys': {'p256dh': subscription.p256dh, 'auth': subscription.auth}},
                    data=payload, vapid_private_key=settings.VAPID_PRIVATE_KEY,
                    vapid_claims={'sub': settings.VAPID_SUBJECT}, timeout=10, ttl=86400)
            if subscription.failed_at:
                PushSubscription.objects.filter(pk=subscription.pk).update(failed_at=None)
        except WebPushException as error:
            status = getattr(error.response, 'status_code', None)
            # 404/410 mean the browser dropped this subscription for good.
            if status in (404, 410):
                PushSubscription.objects.filter(pk=subscription.pk).delete()
            else:
                mark_failed(subscription)
            logger.warning('Push delivery failed status=%s', status)
        except (OSError, ValueError) as error:
            mark_failed(subscription)
            logger.warning('Push delivery failed: %s', type(error).__name__)
        finally:
            connection.close()


def mark_failed(subscription):
    PushSubscription.objects.filter(pk=subscription.pk, failed_at__isnull=True).update(failed_at=timezone.now())


def prune():
    """Drop endpoints that have refused delivery for a month; the browser is gone."""
    deadline = timezone.now() - timedelta(days=STALE_DAYS)
    return PushSubscription.objects.filter(failed_at__lt=deadline).delete()[0]
