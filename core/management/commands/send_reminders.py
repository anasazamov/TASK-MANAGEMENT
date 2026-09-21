from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core import push
from core.models import Event, Notification, Task


class Command(BaseCommand):
    help = 'Create in-app deadline, escalation, and stale-task reminders; safe to run repeatedly.'

    def handle(self, *args, **options):
        delivered = 0
        for task in Task.objects.filter(status='active').enriched():
            state = 'stale' if task.is_stale else task.state
            if state not in ['overdue', 'soon', 'stale']:
                continue
            title = {'overdue': 'Topshiriq muddati o‘tdi', 'soon': 'Topshiriq muddati yaqinlashmoqda',
                     'stale': 'Muddatsiz topshiriq uzoq davom etmoqda'}[state]
            recipients = {task.issuer_id, task.assignee_id}
            if state == 'overdue':
                for ancestor in task.ancestors():
                    recipients.update([ancestor.issuer_id, ancestor.assignee_id])
            with transaction.atomic():
                created_any = False
                for user_id in recipients:
                    key = f'{task.pk}:{state}:{timezone.localdate()}'
                    notification, created = Notification.objects.get_or_create(user_id=user_id, dedupe_key=key, defaults={
                        'task': task, 'title': title if user_id in [task.issuer_id, task.assignee_id] else f'Eskalatsiya: {task.assignee.short_name} topshirig‘i kechikdi',
                    })
                    delivered += int(created)
                    created_any |= created
                    if created:
                        push.send(notification)
                if created_any:
                    Event.objects.create(task=task, kind=Event.Kind.ALERT, body=f'{title}. {task.deadline_text}.')
        dropped = push.prune()
        self.stdout.write(self.style.SUCCESS(
            f'{delivered} notifications created.' + (f' {dropped} dead push subscriptions removed.' if dropped else '')))
