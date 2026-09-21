from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import DeadlineRequest, Event, Notification, Task, TaskAttachment, TaskParticipant
from .permissions import (assignees_for, can_add_participants, can_attach, can_control,
                          can_delegate, can_manage, controllers_for)


def require(condition):
    if not condition:
        raise PermissionDenied('Bu amal uchun ruxsatingiz yo‘q.')


def log(task, actor, kind, body):
    return Event.objects.create(task=task, actor=actor, kind=kind, body=body)


def notify(task, users, title):
    from . import deliveries
    for user_id in {u.pk for u in users if u and u.is_active}:
        deliveries.announce(Notification.objects.create(task=task, user_id=user_id, title=title))


def mark_seen(task, user):
    # update() leaves updated_at alone: agent proposals use it as a change snapshot.
    if task.assignee_id == user.pk and task.seen_at is None:
        task.seen_at = timezone.now()
        Task.objects.filter(pk=task.pk, seen_at__isnull=True).update(seen_at=task.seen_at)


def active_descendants(task):
    pending, seen, result = [task.pk], {task.pk}, []
    while pending:
        children = list(Task.objects.filter(parent_id__in=pending))
        pending = []
        for child in children:
            if child.pk not in seen:
                seen.add(child.pk)
                pending.append(child.pk)
                if child.status != Task.Status.ACCEPTED:
                    result.append(child)
    return result


def validate_deadline(due_at, parent=None, task=None):
    if due_at and due_at <= timezone.now():
        raise ValidationError('Yangi muddat kelajakdagi sana va vaqt bo‘lishi kerak.')
    if parent:
        for ancestor in [*parent.ancestors(), parent]:
            if ancestor.due_at and (not due_at or due_at > ancestor.due_at):
                raise ValidationError('Quyi muddat asosiy topshiriq muddatidan kech bo‘lishi mumkin emas.')
    if task and due_at:
        for child in active_descendants(task):
            if not child.due_at or child.due_at > due_at:
                raise ValidationError('Avval quyi topshiriqlar muddatlarini moslashtiring.')


@transaction.atomic
def create_task(user, data, parent=None):
    require(user.can_assign)
    require(assignees_for(user).filter(pk=data['assignee'].pk).exists())
    if parent:
        parent = Task.objects.select_for_update().get(pk=parent.pk)
        require(can_delegate(user, parent))
    validate_deadline(data.get('due_at'), parent=parent)
    task = Task.objects.create(issuer=user, parent=parent, **data)
    log(task, user, Event.Kind.CREATED, f'{user.short_name} → {task.assignee.short_name}')
    if parent:
        log(parent, user, Event.Kind.DELEGATED, f'{task.code}: {task.title} → {task.assignee.short_name}')
    notify(task, [task.assignee], 'Sizga yangi topshiriq berildi')
    return task


@transaction.atomic
def add_participant(user, task_id, person_id, part, kind=TaskParticipant.Kind.EXECUTOR):
    task = Task.objects.select_for_update().get(pk=task_id)
    require(Task.objects.visible_to(user).filter(pk=task_id).exists() and can_add_participants(user, task))
    controller = kind == TaskParticipant.Kind.CONTROLLER
    part = (part or '').strip()
    if not controller and not 3 <= len(part) <= 240:
        raise ValidationError('Ijro qismini 3–240 belgi bilan yozing.')
    people = controllers_for(user) if controller else assignees_for(user)
    person = people.filter(pk=person_id).first()
    if not person or person.pk in (task.assignee_id, task.issuer_id):
        raise ValidationError('Bu xodimni bu topshiriqqa qo‘sha olmaysiz.')
    if task.participants.filter(user=person).exists():
        raise ValidationError('Bu xodim allaqachon topshiriqqa biriktirilgan.')
    participant = TaskParticipant.objects.create(task=task, user=person, part=part[:240], kind=kind, added_by=user)
    label = 'Nazoratchi' if controller else 'Qo‘shimcha ijrochi'
    log(task, user, Event.Kind.PART, f'{label}: {person.short_name}' + (f' — {part}' if part else ''))
    notify(task, [person], 'Siz topshiriqqa nazoratchi qilib belgilandingiz' if controller
           else 'Sizga topshiriqning bir qismi biriktirildi')
    notify(task, [task.assignee], f'{person.short_name} topshiriqqa {label.lower()} qilib qo‘shildi')
    return participant


@transaction.atomic
def add_attachment(user, task_id, upload):
    from django.conf import settings
    task = Task.objects.get(pk=task_id)
    require(Task.objects.visible_to(user).filter(pk=task_id).exists() and can_attach(user, task))
    if not upload or not upload.size:
        raise ValidationError('Fayl bo‘sh.')
    if upload.size > settings.TASK_FILE_MAX_BYTES:
        raise ValidationError(f'Fayl hajmi {settings.TASK_FILE_MAX_BYTES // (1024*1024)} MB dan oshmasligi kerak.')
    if task.attachments.count() >= 20:
        raise ValidationError('Bitta topshiriqqa 20 tagacha fayl biriktiriladi.')
    name = Path(upload.name).name[:180]
    if Path(name).suffix.lower() not in settings.TASK_FILE_EXTENSIONS:
        raise ValidationError('Ruxsat etilgan turlar: ' + ', '.join(sorted(settings.TASK_FILE_EXTENSIONS)) + '.')
    attachment = TaskAttachment.objects.create(task=task, file=upload, name=name, size=upload.size, uploaded_by=user)
    log(task, user, Event.Kind.PART, f'Fayl biriktirildi: {name}')
    notify(task, [task.assignee, task.issuer], 'Topshiriqqa fayl biriktirildi')
    return attachment


@transaction.atomic
def remove_attachment(user, attachment_id):
    attachment = TaskAttachment.objects.select_related('task').get(pk=attachment_id)
    task = attachment.task
    require(Task.objects.visible_to(user).filter(pk=task.pk).exists()
            and (can_manage(user, task) or attachment.uploaded_by_id == user.pk))
    name = attachment.name
    attachment.file.delete(save=False)
    attachment.delete()
    log(task, user, Event.Kind.PART, f'Fayl o‘chirildi: {name}')


@transaction.atomic
def part_action(user, participant_id, action, text=''):
    participant = TaskParticipant.objects.select_for_update().select_related('task', 'user').get(pk=participant_id)
    task = participant.task
    require(Task.objects.visible_to(user).filter(pk=task.pk).exists())
    text = text.strip()[:10000]
    if participant.kind == TaskParticipant.Kind.CONTROLLER and action != 'remove':
        raise ValidationError('Nazoratchi ijroni topshirmaydi: u kuzatadi, izoh yozadi va hisobot so‘raydi.')
    if action == 'remove':
        require(can_add_participants(user, task))
        participant.delete()
        log(task, user, Event.Kind.PART, f'Qo‘shimcha ijrochi olib tashlandi: {participant.user.short_name}')
        notify(task, [participant.user], 'Topshiriq qismi bekor qilindi')
        return None
    if action == 'submit_part':
        require(participant.user_id == user.pk)
        if participant.status != TaskParticipant.Status.ACTIVE:
            raise ValidationError('Bu qism allaqachon topshirilgan.')
        if not text:
            raise ValidationError('Bajarilgan ish haqida qisqa hisobot yozing.')
        participant.status, participant.submitted_at = TaskParticipant.Status.SUBMITTED, timezone.now()
        log(task, user, Event.Kind.PART, f'«{participant.part}» qismi topshirildi. {text}')
        notify(task, [task.issuer, task.assignee], f'{user.short_name} topshiriq qismini topshirdi')
    elif action in ('accept_part', 'return_part'):
        require(can_add_participants(user, task))
        if participant.status != TaskParticipant.Status.SUBMITTED:
            raise ValidationError('Bu qism hali topshirilmagan.')
        if action == 'accept_part':
            participant.status, participant.accepted_at = TaskParticipant.Status.ACCEPTED, timezone.now()
            log(task, user, Event.Kind.PART, f'«{participant.part}» qismi qabul qilindi. {text}'.strip())
            notify(task, [participant.user], 'Topshiriq qismingiz qabul qilindi')
        else:
            if not text:
                raise ValidationError('Qaytarish sababini yozing.')
            participant.status, participant.submitted_at = TaskParticipant.Status.ACTIVE, None
            log(task, user, Event.Kind.PART, f'«{participant.part}» qismi qayta ishlashga qaytarildi. {text}')
            notify(task, [participant.user], 'Topshiriq qismingiz qaytarildi')
    else:
        raise ValidationError('Noma’lum amal.')
    participant.save()
    return participant


@transaction.atomic
def task_action(user, task_id, action, text='', due_at=None):
    task = Task.objects.select_for_update(of=('self',)).enriched().get(pk=task_id)
    require(Task.objects.visible_to(user).filter(pk=task_id).exists())
    text = text.strip()
    if len(text) > 10000:
        raise ValidationError('Matn 10 000 belgidan oshmasligi kerak.')
    manager = can_manage(user, task)
    owner = task.assignee_id == user.pk
    helper = task.participants.filter(user=user, kind=TaskParticipant.Kind.EXECUTOR).exists()
    controller = can_control(user, task)
    open_parts = task.participants.filter(kind=TaskParticipant.Kind.EXECUTOR).exclude(
        status=TaskParticipant.Status.ACCEPTED).exists()
    mark_seen(task, user)
    if action == 'comment':
        if not text:
            raise ValidationError('Izoh matnini kiriting.')
        log(task, user, Event.Kind.COMMENT, text)
        notify(task, [u for u in [task.issuer, task.assignee] if u.pk != user.pk], 'Yangi izoh qo‘shildi')
        return task
    if task.status == Task.Status.ACCEPTED:
        raise ValidationError('Qabul qilingan topshiriq yopilgan.')
    if action == 'submit':
        require(owner)
        if task.status != Task.Status.ACTIVE:
            raise ValidationError('Ijro allaqachon topshirilgan.')
        if active_descendants(task):
            raise ValidationError('Avval barcha quyi topshiriqlar ijrosi qabul qilinishi kerak.')
        if open_parts:
            raise ValidationError('Avval qo‘shimcha ijrochilar qismlari qabul qilinishi kerak.')
        if not text:
            raise ValidationError('Bajarilgan ish haqida qisqa hisobot yozing.')
        task.status = Task.Status.SUBMITTED
        log(task, user, Event.Kind.SUBMITTED, text)
        notify(task, [task.issuer], 'Ijro topshirildi — qaroringiz kutilmoqda')
    elif action in ['accept', 'return']:
        require(manager)
        if task.status != Task.Status.SUBMITTED:
            raise ValidationError('Topshiriq ijrosi hali topshirilmagan.')
        if action == 'accept':
            if active_descendants(task):
                raise ValidationError('Quyi topshiriqlar hali yopilmagan.')
            task.status = Task.Status.ACCEPTED
            task.accepted_at = timezone.now()
            log(task, user, Event.Kind.ACCEPTED, 'Bajarilgan ish tekshirildi va qabul qilindi.')
            task.deadline_requests.filter(state='pending').update(state='rejected', resolved_by=user, resolved_at=timezone.now())
            notify(task, [task.assignee], 'Ijro qabul qilindi')
        else:
            if not text:
                raise ValidationError('Qaytarish sababini yozish majburiy.')
            task.status = Task.Status.ACTIVE
            log(task, user, Event.Kind.RETURNED, text)
            notify(task, [task.assignee], 'Topshiriq qayta ishlashga qaytarildi')
    elif action == 'set_deadline':
        require(manager)
        if task.status != Task.Status.ACTIVE:
            raise ValidationError('Faqat jarayondagi topshiriq muddatini o‘zgartirish mumkin.')
        validate_deadline(due_at, parent=task.parent, task=task)
        old = timezone.localtime(task.due_at).strftime('%d.%m.%Y %H:%M') if task.due_at else 'Muddatsiz'
        task.due_at = due_at
        new = timezone.localtime(due_at).strftime('%d.%m.%Y %H:%M') if due_at else 'Muddatsiz'
        log(task, user, Event.Kind.DEADLINE, f'Muddat o‘zgartirildi: {old} → {new}. {text}')
        notify(task, [task.assignee], 'Topshiriq muddati o‘zgartirildi')
    elif action == 'request_deadline':
        require(owner)
        if task.status != Task.Status.ACTIVE:
            raise ValidationError('Faqat jarayondagi topsshiriq uchun so‘rov beriladi.')
        if not text or not due_at:
            raise ValidationError('Yangi muddat va sababni kiriting.')
        validate_deadline(due_at, parent=task.parent, task=task)
        if task.due_at and due_at <= task.due_at:
            raise ValidationError('So‘ralayotgan muddat amaldagi muddatdan keyin bo‘lishi kerak.')
        if task.deadline_requests.filter(state='pending').exists():
            raise ValidationError('Oldingi so‘rovingiz bo‘yicha qaror kutilmoqda.')
        DeadlineRequest.objects.create(task=task, requester=user, proposed_due_at=due_at, reason=text)
        log(task, user, Event.Kind.DEADLINE, f'Muddat uzaytirish so‘raldi: {timezone.localtime(due_at):%d.%m.%Y %H:%M}. Sabab: {text}')
        notify(task, [task.issuer], 'Muddatni uzaytirish so‘rovi')
    elif action in ['approve_deadline', 'reject_deadline']:
        require(manager)
        request = task.deadline_requests.select_for_update().filter(state='pending').first()
        if not request:
            raise ValidationError('Ko‘rib chiqiladigan so‘rov yo‘q.')
        if action == 'approve_deadline':
            validate_deadline(request.proposed_due_at, parent=task.parent, task=task)
            task.due_at = request.proposed_due_at
            request.state = 'approved'
            body = f'Muddat so‘rovi tasdiqlandi: {timezone.localtime(task.due_at):%d.%m.%Y %H:%M}.'
        else:
            if not text:
                raise ValidationError('Rad etish sababini yozing.')
            request.state = 'rejected'
            body = f'Muddat so‘rovi rad etildi. Sabab: {text}'
        request.resolved_by, request.resolved_at = user, timezone.now()
        request.save()
        log(task, user, Event.Kind.DEADLINE, body)
        notify(task, [task.assignee], body[:240])
    elif action == 'request_report':
        require(manager or controller)
        task.report_requested_at = timezone.now()
        log(task, user, Event.Kind.REPORT, 'Haftalik hisobot so‘raldi.')
        notify(task, [task.assignee], 'Haftalik hisobot yuborishingiz so‘raldi')
    elif action == 'report':
        require(owner or helper)
        if not text:
            raise ValidationError('Hisobot matnini kiriting.')
        task.last_report_at = timezone.now()
        log(task, user, Event.Kind.REPORT, text)
        notify(task, [task.issuer], 'Haftalik hisobot taqdim etildi')
    else:
        raise ValidationError('Noma’lum amal.')
    task.save()
    return task
