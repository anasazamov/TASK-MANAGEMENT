import math
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone


class Department(models.Model):
    name = models.CharField("Bo‘linma nomi", max_length=180, unique=True)
    head = models.OneToOneField('User', null=True, blank=True, on_delete=models.SET_NULL, related_name='led_department')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        CHAIR = 'chair', 'Boshqaruv raisi'
        DEPUTY = 'deputy', 'Rais o‘rinbosari'
        HEAD = 'head', 'Bo‘lim boshlig‘i'
        OFFICE = 'office', 'Devonxona mudiri'
        SECRETARY = 'secretary', 'Kotiba'
        EMPLOYEE = 'employee', 'Xodim'

    full_name = models.CharField('F.I.Sh.', max_length=180)
    job_title = models.CharField('Lavozimi', max_length=180, blank=True)
    phone = models.CharField('Telefon', max_length=20, blank=True,
                             help_text='Telegram orqali kirish uchun: 998901234567')
    telegram_id = models.BigIntegerField('Telegram ID', null=True, blank=True, unique=True, editable=False)
    role = models.CharField('Rol', max_length=12, choices=Role.choices, default=Role.EMPLOYEE)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT, related_name='employees')
    # A deputy works across departments rather than inside one: these are the
    # departments they answer for, and the whole of their reach is derived here.
    supervised = models.ManyToManyField(Department, blank=True, related_name='deputies',
                                        verbose_name='Nazorat qiladigan bo‘linmalar')

    class Meta:
        ordering = ['full_name', 'username']

    @property
    def short_name(self):
        parts = self.full_name.split()
        def initial(part):
            return (part[:2] if part.lower().startswith(('sh', 'ch', 'yu', 'ya')) else part[0]) + '.'
        return f"{parts[0]} {''.join(initial(p) for p in parts[1:])}" if parts else self.username

    @property
    def initials(self):
        return ''.join(p[0] for p in self.full_name.split()[:2]).upper() or self.username[:2].upper()

    @property
    def is_chair(self):
        return self.role == self.Role.CHAIR

    @property
    def is_deputy(self):
        return self.role == self.Role.DEPUTY

    @property
    def supervised_ids(self):
        return list(self.supervised.values_list('pk', flat=True)) if self.is_deputy else []

    @property
    def is_office(self):
        return self.role == self.Role.OFFICE

    @property
    def is_secretary(self):
        return self.role == self.Role.SECRETARY

    @property
    def can_assign(self):
        return self.role in [self.Role.CHAIR, self.Role.DEPUTY, self.Role.HEAD,
                             self.Role.OFFICE, self.Role.SECRETARY]

    @property
    def can_oversee(self):
        """Sees every task and the employee statistics across all departments."""
        return self.role in [self.Role.CHAIR, self.Role.SECRETARY]

    def __str__(self):
        return self.short_name


class VoiceProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='voice_profile')
    encrypted_embedding = models.TextField(editable=False)
    model_version = models.CharField(max_length=64, editable=False)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    consent_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)


class TaskQuerySet(models.QuerySet):
    def visible_to(self, user):
        if not user.is_authenticated:
            return self.none()
        if user.can_oversee:
            return self
        if user.is_office:
            # The office registers incoming letters; it follows what it sent out.
            return self.filter(Q(issuer=user) | Q(assignee=user) | Q(pk__in=TaskParticipant.objects.filter(user=user).values('task')))
        # Subquery, not a join: an extra participant must not duplicate task rows.
        parts = TaskParticipant.objects.filter(user=user).values('task')
        if user.is_deputy:
            # Everything inside the departments this deputy answers for, plus
            # whatever they were given or took part in elsewhere.
            departments = user.supervised_ids
            parts = TaskParticipant.objects.filter(
                Q(user__department_id__in=departments) | Q(user=user)).values('task')
            return self.filter(Q(assignee__department_id__in=departments) | Q(issuer=user)
                               | Q(assignee=user) | Q(pk__in=parts))
        if user.role == User.Role.HEAD and user.department_id:
            parts = TaskParticipant.objects.filter(
                Q(user__department_id=user.department_id) | Q(user=user)).values('task')
            return self.filter(Q(assignee__department_id=user.department_id) | Q(issuer=user)
                               | Q(assignee=user) | Q(pk__in=parts))
        return self.filter(Q(assignee=user) | Q(pk__in=parts))

    def enriched(self):
        return self.select_related('issuer', 'assignee', 'assignee__department', 'parent', 'parent__issuer', 'parent__assignee')


class Task(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Jarayonda'
        SUBMITTED = 'submitted', 'Tasdiq kutilmoqda'
        ACCEPTED = 'accepted', 'Qabul qilingan'

    title = models.CharField('Topshiriq mazmuni', max_length=240)
    description = models.TextField('Izoh va talablar', blank=True)
    issuer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='issued_tasks')
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assigned_tasks')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    due_at = models.DateTimeField('Muddat', null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    report_requested_at = models.DateTimeField(null=True, blank=True)
    last_report_at = models.DateTimeField(null=True, blank=True)
    seen_at = models.DateTimeField('Ijrochi tanishgan vaqt', null=True, blank=True)
    letter_number = models.CharField('Xat raqami', max_length=60, blank=True)
    letter_date = models.DateField('Xat sanasi', null=True, blank=True)
    letter_sender = models.CharField('Xat kimdan kelgan', max_length=180, blank=True)
    objects = TaskQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'due_at']), models.Index(fields=['assignee', 'status'])]
        constraints = [models.CheckConstraint(condition=~Q(issuer=models.F('assignee')), name='task_different_participants')]

    def __str__(self):
        return f'{self.code} — {self.title}'

    def get_absolute_url(self):
        return reverse('task_detail', args=[self.pk])

    @property
    def code(self):
        return f'T-{self.pk + 100}'

    @property
    def state(self):
        if self.status != self.Status.ACTIVE:
            return self.status
        if not self.due_at:
            return 'undated'
        if self.due_at < timezone.now():
            return 'overdue'
        if self.due_at <= timezone.now() + timedelta(days=settings.DUE_SOON_DAYS):
            return 'soon'
        return 'active'

    @property
    def state_label(self):
        return {'active': 'Jarayonda', 'submitted': 'Tasdiq kutilmoqda', 'accepted': 'Qabul qilingan',
                'overdue': 'Muddati o‘tgan', 'soon': 'Muddati yaqin', 'undated': 'Muddatsiz'}[self.state]

    @property
    def age_days(self):
        return max(0, (timezone.localdate() - timezone.localtime(self.created_at).date()).days)

    @property
    def is_stale(self):
        return not self.due_at and self.status == self.Status.ACTIVE and self.age_days >= settings.STALE_DAYS

    @property
    def deadline_text(self):
        if self.status == self.Status.ACCEPTED:
            return 'Ijro qabul qilingan'
        if self.status == self.Status.SUBMITTED:
            return 'Ijro topshirilgan'
        if not self.due_at:
            return f'Berilganiga {self.age_days} kun bo‘ldi'
        hours = (self.due_at - timezone.now()).total_seconds() / 3600
        if hours < 0:
            return f'{max(1, math.floor(-hours / 24))} kun kechikdi'
        if hours < 24:
            return f'{max(1, math.ceil(hours))} soat qoldi'
        return f'{math.ceil(hours / 24)} kun qoldi'

    @property
    def report_label(self):
        if self.report_requested_at and (not self.last_report_at or self.last_report_at < self.report_requested_at):
            return 'Hisobot so‘ralgan — javob kutilmoqda'
        return 'Hisobot berilgan' if self.last_report_at else 'Hisobot so‘ralmagan'

    def ancestors(self):
        result, current, seen = [], self.parent, {self.pk}
        while current and current.pk not in seen:
            result.append(current)
            seen.add(current.pk)
            current = current.parent
        return list(reversed(result))


def attachment_path(instance, filename):
    return f'tasks/{instance.task_id}/{uuid.uuid4().hex}/{filename}'[:300]


class TaskAttachment(models.Model):
    """Incoming letters and related documents, stored in S3/MinIO, not in the database."""
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField('Fayl', upload_to=attachment_path, max_length=300)
    name = models.CharField(max_length=180)
    size = models.PositiveBigIntegerField()
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return self.name

    @property
    def size_label(self):
        return f'{self.size/1024:.0f} KB' if self.size < 1024*1024 else f'{self.size/1024/1024:.1f} MB'


class TaskParticipant(models.Model):
    """An extra executor with a named part, or a controller who only follows the task."""
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Bajarilmoqda'
        SUBMITTED = 'submitted', 'Topshirildi'
        ACCEPTED = 'accepted', 'Qabul qilindi'

    class Kind(models.TextChoices):
        EXECUTOR = 'executor', 'Qo‘shimcha ijrochi'
        CONTROLLER = 'controller', 'Nazoratchi'

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='task_parts')
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.EXECUTOR)
    part = models.CharField('Ijro qismi', max_length=240, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['pk']
        constraints = [models.UniqueConstraint(fields=['task', 'user'], name='one_part_per_participant')]

    def __str__(self):
        return f'{self.user.short_name}: {self.part}'

    @property
    def status_label(self):
        return self.Status(self.status).label


class DeadlineRequest(models.Model):
    class State(models.TextChoices):
        PENDING = 'pending', 'Kutilmoqda'
        APPROVED = 'approved', 'Tasdiqlandi'
        REJECTED = 'rejected', 'Rad etildi'

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='deadline_requests')
    requester = models.ForeignKey(User, on_delete=models.PROTECT, related_name='+')
    proposed_due_at = models.DateTimeField()
    reason = models.TextField()
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True)
    resolved_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['task'], condition=Q(state='pending'), name='one_pending_deadline_request')]


class Event(models.Model):
    class Kind(models.TextChoices):
        CREATED = 'created', 'Topshiriq berildi'
        DELEGATED = 'delegated', 'Taqsimlandi'
        SUBMITTED = 'submitted', 'Ijro topshirildi'
        ACCEPTED = 'accepted', 'Ijro qabul qilindi'
        RETURNED = 'returned', 'Qaytarildi'
        DEADLINE = 'deadline', 'Muddat'
        REPORT = 'report', 'Hisobot'
        COMMENT = 'comment', 'Izoh'
        ALERT = 'alert', 'Ogohlantirish'
        PART = 'part', 'Ijro qismi'

    # History follows the task it describes: nothing may delete an event on its
    # own, but a task removed in the admin takes its own record with it.
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='events')
    actor = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=16, choices=Kind.choices)
    body = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    title = models.CharField(max_length=240)
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=160, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['user', 'dedupe_key'], name='unique_notification_delivery')]


class GeneratedPage(models.Model):
    """An agent-built page: its layout is model-written HTML, its numbers are not."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='generated_pages')
    title = models.CharField('Sahifa nomi', max_length=80)
    html = models.TextField(editable=False)
    tool = models.JSONField(editable=False)
    arguments = models.JSONField(default=dict, editable=False)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('generated_page', args=[self.pk])


class PushSubscription(models.Model):
    """One browser's push endpoint; the keys encrypt the payload for that browser only."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    created_at = models.DateTimeField(default=timezone.now)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.short_name}: {self.endpoint[:40]}…'


class AgentConversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    messages = models.JSONField(default=list)
    tools = models.JSONField(default=dict)
    references = models.JSONField(default=list)
    scope = models.CharField(max_length=100, blank=True)
    busy_until = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)


class AgentTurn(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    conversation = models.ForeignKey(AgentConversation, on_delete=models.CASCADE)
    fingerprint = models.CharField(max_length=64)
    response = models.JSONField(null=True)
    created_at = models.DateTimeField(default=timezone.now)


class AgentProposal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(AgentConversation, on_delete=models.CASCADE)
    payload = models.JSONField()
    preview = models.JSONField()
    snapshot = models.CharField(max_length=100, blank=True)
    expires_at = models.DateTimeField()
    state = models.CharField(max_length=12, default='pending')
    result = models.JSONField(null=True)
