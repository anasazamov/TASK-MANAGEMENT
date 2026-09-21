from django.db import migrations, models
from django.db.models import Min, OuterRef, Subquery


def backfill(apps, schema_editor):
    # Before tracking existed, a task counts as seen once its assignee acted on it.
    Task = apps.get_model('core', 'Task')
    Event = apps.get_model('core', 'Event')
    first_action = (Event.objects.filter(task=OuterRef('pk'), actor=OuterRef('assignee'))
                    .values('task').annotate(first=Min('created_at')).values('first'))
    Task.objects.filter(seen_at__isnull=True).update(seen_at=Subquery(first_action))
    for task in Task.objects.filter(seen_at__isnull=True).exclude(status='active'):
        Task.objects.filter(pk=task.pk).update(seen_at=task.accepted_at or task.updated_at)


class Migration(migrations.Migration):
    dependencies = [('core', '0004_agentconversation_tools')]
    operations = [
        migrations.AddField(model_name='task', name='seen_at',
                            field=models.DateTimeField(blank=True, null=True, verbose_name='Ijrochi tanishgan vaqt')),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
