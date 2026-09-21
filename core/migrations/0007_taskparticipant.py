import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL), ('core', '0006_generatedpage')]
    operations = [
        migrations.AlterField(
            model_name='event', name='kind',
            field=models.CharField(choices=[('created', 'Topshiriq berildi'), ('delegated', 'Taqsimlandi'),
                                            ('submitted', 'Ijro topshirildi'), ('accepted', 'Ijro qabul qilindi'),
                                            ('returned', 'Qaytarildi'), ('deadline', 'Muddat'), ('report', 'Hisobot'),
                                            ('comment', 'Izoh'), ('alert', 'Ogohlantirish'), ('part', 'Ijro qismi')],
                                   max_length=16),
        ),
        migrations.CreateModel(
            name='TaskParticipant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('part', models.CharField(max_length=240, verbose_name='Ijro qismi')),
                ('status', models.CharField(choices=[('active', 'Bajarilmoqda'), ('submitted', 'Topshirildi'),
                                                     ('accepted', 'Qabul qilindi')], default='active', max_length=12)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('accepted_at', models.DateTimeField(blank=True, null=True)),
                ('added_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='participants', to='core.task')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='task_parts', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['pk']},
        ),
        migrations.AddConstraint(
            model_name='taskparticipant',
            constraint=models.UniqueConstraint(fields=('task', 'user'), name='one_part_per_participant'),
        ),
    ]
