"""Attendance (yo'qlama) ingest from the Hikvision door station.

Events arrive at /attendance/hook/<secret>/ — either pushed by the device, or
(in this deployment) forwarded by the LAN bridge agent that reads the device's
access-event log. Each POST carries one access event as JSON and, optionally, a
face snapshot. This module turns a POST into one `AccessEvent` and folds it into
the person's daily `Attendance`.

The face snapshot is NOT stored here: the station is backlit, so most snapshots
are dark and worthless. The first-entry face check (added next) keeps a snapshot
only when it was not confident and a human must review it.

Everything received is untrusted data, never instructions.
"""
import json
import logging
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (AccessCard, AccessEvent, Attendance, AttendanceAlert, AttendanceExcuse,
                     FaceEnrollment, FaceReview, User)

MAX_EXCUSE_DAYS = 62

logger = logging.getLogger(__name__)


def secretaries():
    return User.objects.filter(is_active=True, role=User.Role.SECRETARY)


def alert_once(subject, day, kind):
    """Record that this once-a-day alert was handled; True only the first time."""
    try:
        with transaction.atomic():
            AttendanceAlert.objects.create(subject=subject, date=day, kind=kind)
        return True
    except IntegrityError:
        return False


def send_arrival(employee_id, first_entry_at):
    """Tell the secretaries an employee has arrived (their first entry today)."""
    from . import telegram
    if not telegram.configured():
        return
    employee = User.objects.filter(pk=employee_id).first()
    if not employee:
        return
    when = timezone.localtime(first_entry_at).strftime('%H:%M')
    text = f'{employee.full_name} ishga keldi — soat {when}.'
    for secretary in secretaries():
        telegram.message(secretary, text)


def send_review_alert(review_id):
    """Tell the secretaries a first-entry face check needs their decision."""
    from . import telegram
    if not telegram.configured():
        return
    review = FaceReview.objects.filter(pk=review_id).select_related('user').first()
    if not review:
        return
    detail = 'kadrda yuz topilmadi' if review.confidence is None else f'moslik {review.confidence:.0%}'
    text = f'Yuz tekshiruvi kerak: {review.user.full_name} ({detail}). Tasdiqlang yoki rad eting.'
    for secretary in secretaries():
        telegram.message(secretary, text, '/employees/reviews/')


def send_pending_notice(employee_id, entered_at, confidence):
    """Tell the employee their entry was sent for review, and how to settle it."""
    from . import telegram
    if not telegram.configured():
        return
    employee = User.objects.filter(pk=employee_id).first()
    if not employee:
        return
    why = 'kadrda yuzingiz topilmadi' if confidence is None else 'yuzingiz yetarli aniq tanilmadi'
    telegram.message(employee, f'{timezone.localtime(entered_at):%H:%M} dagi kirishingizda {why}, shuning uchun '
                               f'bugungi yo‘qlamangiz kotib referent tekshiruviga yuborildi. Keyingi kirishda '
                               f'yuzingizni kameraga to‘g‘ri ko‘rsating — yo‘qlama avtomatik tasdiqlanadi.')


def send_not_counted(employee_id, day, reason=''):
    """Tell an employee their attendance was not accepted after review, and why."""
    from . import telegram
    if not telegram.configured():
        return
    employee = User.objects.filter(pk=employee_id).first()
    if employee:
        why = f' Sabab: {reason}.' if reason else ''
        telegram.message(employee, f'Bugungi ({day:%d.%m.%Y}) yo‘qlamangiz tasdiqlanmadi.{why} '
                                   f'Iltimos, kotib referentga murojaat qiling.')


def send_reverified(employee_id, confidence, previous):
    """Tell the secretaries a rejected or pending day was confirmed by a later clear face."""
    from . import telegram
    if not telegram.configured():
        return
    employee = User.objects.filter(pk=employee_id).first()
    if not employee:
        return
    if previous == Attendance.Status.REVIEW:
        text = (f'{employee.full_name}: keyingi kirishda yuz tasdiqlandi (moslik {confidence:.0%}), holat — '
                f'«Keldi». Oldingi kirish bo‘yicha tekshiruv ochiq: tasdiqlasangiz, kelgan vaqti o‘sha kirishga suriladi.')
    else:
        text = (f'{employee.full_name}: rad etilgan yo‘qlama keyingi kirishda yuz bilan tasdiqlandi '
                f'(moslik {confidence:.0%}). Holat — «Keldi».')
    for secretary in secretaries():
        telegram.message(secretary, text)


def settle_day(attendance):
    """Re-derive a day from its entries; the single place a day's status is decided.

    An entry counts unless its face review is pending or was rejected. The day is
    present if any entry counts, and the person arrived at the earliest one that
    does — so approving an earlier doubtful entry moves the arrival back to it. With
    nothing counting, the day is in review while any decision is open, else rejected.
    """
    start = timezone.make_aware(datetime.combine(attendance.date, time(settings.ATTENDANCE_DAY_START_HOUR)))
    end = timezone.make_aware(datetime.combine(attendance.date + timedelta(days=1), time.min))
    entries = list(AccessEvent.objects.filter(user=attendance.user, happened_at__gte=start,
                                              happened_at__lt=end).order_by('happened_at', 'pk'))
    if not entries:
        return attendance
    states = dict(FaceReview.objects.filter(event__in=entries).values_list('event_id', 'state'))
    counted = [e for e in entries if states.get(e.pk) not in (FaceReview.State.PENDING, FaceReview.State.REJECTED)]
    if counted:
        attendance.status, first = Attendance.Status.PRESENT, counted[0]
    elif FaceReview.State.PENDING in states.values():
        attendance.status, first = Attendance.Status.REVIEW, entries[0]
    else:
        attendance.status, first = Attendance.Status.REJECTED, entries[0]
    attendance.first_entry_at, attendance.first_event = first.happened_at, first
    attendance.last_entry_at = entries[-1].happened_at
    attendance.save(update_fields=['status', 'first_entry_at', 'first_event', 'last_entry_at'])
    return attendance


def _verify_face(access, attendance_id, picture, day):
    """Check this entry's face; keep the snapshot only when unsure.

    Runs on the day's first entry, and again on any entry while the day is in
    review or rejected. Every doubtful entry gets its own review — the first one may
    be the real arrival, and if the secretary approves it, that time is what counts.
    High confidence -> snapshot discarded; on a day still in doubt the entry is
    recorded as an automatic approval for the audit trail, without deciding the
    earlier reviews for her. Low confidence, or a frame with no findable face
    (hidden, turned away, too dark) -> snapshot saved, review opened, secretaries
    alerted. A technical gap — no enrolled face, the face subsystem down, no frame —
    opens nothing, so the entry simply counts and an outage never blocks anyone.
    `settle_day` then decides the day.
    """
    from django.core.files.base import ContentFile
    from . import face
    enrollment = FaceEnrollment.objects.filter(user=access.user).first()
    if not enrollment or not face.available():
        return
    try:
        picture_bytes = picture.read()
    except (OSError, ValueError):
        return
    if not picture_bytes:
        return
    # The station squeezes its snapshot sideways; compare (and keep for review) the
    # frame at its true shape, as the secretary would see the person.
    picture_bytes = face.restore_aspect(picture_bytes)
    confidence = face.compare(enrollment.embedding, picture_bytes)  # None: no face in frame
    access.face_confidence = confidence
    attendance = Attendance.objects.filter(pk=attendance_id).first()
    previous = attendance.status if attendance else None
    if confidence is not None and confidence >= settings.FACE_MATCH_THRESHOLD:
        access.save(update_fields=['face_confidence'])
        if previous in (Attendance.Status.REJECTED, Attendance.Status.REVIEW):
            FaceReview.objects.create(
                event=access, user=access.user, attendance=attendance, confidence=confidence,
                state=FaceReview.State.APPROVED, resolved_at=timezone.now(),
                reason=f'Keyingi kirishda yuz avtomatik tasdiqlandi (moslik {confidence:.2f}).')
            transaction.on_commit(lambda uid=access.user_id, c=confidence, p=previous: send_reverified(uid, c, p))
        return
    access.photo.save(f'{access.pk}.jpg', ContentFile(picture_bytes), save=False)
    access.save(update_fields=['face_confidence', 'photo'])
    review = FaceReview.objects.create(event=access, user=access.user,
                                       attendance=attendance, confidence=confidence)
    # Every review gets its own alert: a second one the same day must not go unseen.
    # The employee hears of it too, with the way out: show the face next time.
    transaction.on_commit(lambda rid=review.pk: send_review_alert(rid))
    transaction.on_commit(lambda uid=access.user_id, at=access.happened_at, c=confidence:
                          send_pending_notice(uid, at, c))


def visible_employees(user):
    """Employees whose attendance this user may see, by role.

    Mirrors the task visibility idea: the chair and secretary see everyone, a
    deputy sees the departments they answer for, a head their own department, and
    anyone else only themselves.
    """
    people = User.objects.filter(is_active=True)
    if user.is_chair or user.can_oversee:
        return people
    if user.is_deputy:
        return people.filter(department_id__in=user.supervised_ids)
    if user.role == User.Role.HEAD and user.department_id:
        return people.filter(department_id=user.department_id)
    return people.filter(pk=user.pk)


def source_allowed(request):
    """Optional gate: accept the POST only from known source IPs, if configured."""
    allowed = [ip.strip() for ip in settings.ATTENDANCE_ALLOWED_IPS.split(',') if ip.strip()]
    return not allowed or request.META.get('REMOTE_ADDR', '') in allowed


def parse_request(request):
    """Pull the event dict (and any face image) out of the POST, defensively."""
    content_type = request.META.get('CONTENT_TYPE', '')
    if 'multipart' in content_type.lower():
        raw = request.POST.get('event')
        picture = request.FILES.get('picture')
        try:
            event = json.loads(raw) if raw else None
        except ValueError:
            event = None
        return (event if isinstance(event, dict) else None), picture
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return None, None
    return (data if isinstance(data, dict) else None), None


def _aware(time_text):
    try:
        naive = datetime.strptime(time_text, '%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return None
    return timezone.make_aware(naive)


def _dedupe_key(event, time_text, card_no, employee_no):
    serial = event.get('serialNo')
    if serial:
        return ('s:' + str(serial))[:190]
    return '|'.join([time_text or '', card_no, employee_no,
                     str(event.get('minor', '')), str(event.get('doorNo', ''))])[:190]


def _match_card(card_no, employee_no):
    card = None
    if card_no:
        card = AccessCard.objects.filter(active=True, card_no=card_no).select_related('user').first()
    if not card and employee_no:
        card = AccessCard.objects.filter(active=True, device_employee_no=employee_no).select_related('user').first()
    return card


def is_person_event(event):
    """A card/PIN authentication that names someone — the kind that marks attendance."""
    major = event.get('major')
    return major == settings.ATTENDANCE_DOOR_MAJOR and bool(event.get('cardNo') or event.get('employeeNoString'))


@transaction.atomic
def ingest_event(event, picture=None):
    """Record one access event and fold it into the day's attendance.

    Returns a small dict describing what happened; the view ignores it but tests
    and later the face check read it. `picture` is accepted but not stored yet.
    """
    if not is_person_event(event):
        return {'stored': False, 'reason': 'not_a_person_event'}
    time_text = event.get('time')
    happened_at = _aware(time_text)
    if not happened_at:
        return {'stored': False, 'reason': 'bad_time'}
    card_no = str(event.get('cardNo') or '')[:64]
    employee_no = str(event.get('employeeNoString') or '')[:64]
    key = _dedupe_key(event, time_text, card_no, employee_no)
    if AccessEvent.objects.filter(dedupe_key=key).exists():
        return {'stored': False, 'reason': 'duplicate'}
    card = _match_card(card_no, employee_no)
    access = AccessEvent.objects.create(
        user=card.user if card else None, card=card, card_no=card_no, employee_no=employee_no,
        device_name=str(event.get('name') or '')[:180], happened_at=happened_at,
        major=int(event.get('major') or 0), minor=int(event.get('minor') or 0),
        door_no=int(event.get('doorNo') or 0), dedupe_key=key)
    result = {'stored': True, 'event_id': access.pk, 'matched': bool(card),
              'user_id': card.user_id if card else None, 'first_entry': False}
    if card:
        fold = _fold_into_day(card.user, access)
        result.update(fold)
        # The face is checked on the day's first entry, and again on any entry while
        # the day is still in doubt; then the day is re-derived from all its entries.
        if fold.get('first_entry') or fold.get('recheck'):
            if picture is not None:
                _verify_face(access, fold.get('attendance_id'), picture, fold['date'])
            settle_day(Attendance.objects.get(pk=fold['attendance_id']))
        # Tell the secretaries the employee arrived (once), after the row commits.
        if fold.get('first_entry') and alert_once(card.user, fold['date'], AttendanceAlert.Kind.ARRIVAL):
            transaction.on_commit(lambda uid=card.user_id, at=access.happened_at: send_arrival(uid, at))
    return result


def _fold_into_day(user, access):
    """Add this entry to the user's daily attendance, counting only from 07:00."""
    local = timezone.localtime(access.happened_at)
    if local.hour < settings.ATTENDANCE_DAY_START_HOUR:
        return {'counted': False, 'reason': 'before_day_start'}
    attendance, _ = Attendance.objects.select_for_update().get_or_create(
        user=user, date=local.date(),
        defaults={'first_entry_at': access.happened_at, 'last_entry_at': access.happened_at,
                  'entry_count': 0, 'first_event': access})
    first_entry = attendance.entry_count == 0
    recheck = attendance.status in (Attendance.Status.REJECTED, Attendance.Status.REVIEW)
    attendance.entry_count += 1
    if not attendance.first_entry_at or access.happened_at < attendance.first_entry_at:
        attendance.first_entry_at = access.happened_at
        attendance.first_event = access
    if not attendance.last_entry_at or access.happened_at > attendance.last_entry_at:
        attendance.last_entry_at = access.happened_at
    attendance.save()
    return {'counted': True, 'first_entry': first_entry, 'recheck': recheck,
            'attendance_id': attendance.pk, 'date': local.date()}


def late_minutes(attendance):
    """How many minutes after ATTENDANCE_LATE_HOUR (09:00) the person arrived.

    Only a confirmed day has a trustworthy arrival time; anything else, or an
    arrival on time, is None.
    """
    if not attendance or attendance.status != Attendance.Status.PRESENT or not attendance.first_entry_at:
        return None
    start = timezone.make_aware(datetime.combine(attendance.date, time(settings.ATTENDANCE_LATE_HOUR)))
    minutes = int((attendance.first_entry_at - start).total_seconds() // 60)
    return minutes if minutes > 0 else None


def duration_label(minutes):
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f'{hours} soat {rest} daq'
    return f'{hours} soat' if hours else f'{rest} daq'


@transaction.atomic
def set_excuse(actor, employee, kind, start, end=None, note='', expected_at=None):
    """Record why someone is away or late, for one day or a run of days."""
    if kind not in AttendanceExcuse.Kind.values:
        raise ValidationError('Sabab turini tanlang.')
    end = end or start
    if end < start:
        raise ValidationError('Tugash sanasi boshlanishidan oldin bo‘lmasin.')
    if (end - start).days >= MAX_EXCUSE_DAYS:
        raise ValidationError(f'Bir yo‘la {MAX_EXCUSE_DAYS} kundan ko‘p kiritib bo‘lmaydi.')
    note = (note or '').strip()[:300]
    if kind == AttendanceExcuse.Kind.OTHER and not note:
        raise ValidationError('«Boshqa sabab» uchun izoh yozing.')
    if kind != AttendanceExcuse.Kind.LATE:
        expected_at = None
    day, saved = start, 0
    while day <= end:
        AttendanceExcuse.objects.update_or_create(user=employee, date=day, defaults={
            'kind': kind, 'note': note, 'expected_at': expected_at, 'created_by': actor})
        day += timedelta(days=1)
        saved += 1
    return saved


def enroll_face(user, image_file):
    """Store a user's reference face: keep the photo and its computed embedding."""
    from django.core.files.base import ContentFile
    from . import face
    try:
        data = image_file.read()
    except (OSError, ValueError):
        raise ValidationError('Surat o‘qilmadi. Boshqa fayl yuklang.')
    embedding = face.embed(data)
    if embedding is None:
        raise ValidationError('Suratda yuz aniqlanmadi yoki yuz tizimi mavjud emas. '
                              'Aniq, yorug‘ va to‘g‘ri qaragan surat yuklang.')
    enrollment = FaceEnrollment.objects.filter(user=user).first() or FaceEnrollment(user=user)
    enrollment.embedding = embedding
    enrollment.model = 'opencv-sface'
    enrollment.photo.save(f'{user.pk}.jpg', ContentFile(data), save=False)
    enrollment.save()
    return enrollment


@transaction.atomic
def resolve_review(user, review_id, approve, reason=''):
    """Secretary's decision on a face review: accept the day, or reject and warn.

    A rejection must say why; the reason stays on the review for audit and goes to
    the employee. An approval may carry a note too.
    """
    reason = (reason or '').strip()[:2000]
    if not approve and not reason:
        raise ValidationError('Rad etish sababini yozing — u audit uchun saqlanadi.')
    # Lock only the review row: attendance is a nullable FK, so the join is outer and
    # PostgreSQL refuses FOR UPDATE on its nullable side.
    review = (FaceReview.objects.select_for_update(of=('self',)).select_related('attendance', 'user')
              .filter(pk=review_id, state=FaceReview.State.PENDING).first())
    if not review:
        raise ValidationError('Tekshiruv topilmadi yoki allaqachon hal qilingan.')
    review.state = FaceReview.State.APPROVED if approve else FaceReview.State.REJECTED
    review.resolved_at, review.resolved_by, review.reason = timezone.now(), user, reason
    review.save(update_fields=['state', 'resolved_at', 'resolved_by', 'reason'])
    day = settle_day(review.attendance) if review.attendance else None
    # Warn the employee only when the day as a whole is lost: rejecting one doubtful
    # entry on a day another entry already confirmed costs them nothing.
    if not approve and (day is None or day.status == Attendance.Status.REJECTED):
        when = day.date if day else timezone.localdate()
        transaction.on_commit(lambda uid=review.user_id, d=when, r=reason: send_not_counted(uid, d, r))
    return review


def record(request):
    """Entry point for the webhook: parse the POST and ingest one event."""
    event, picture = parse_request(request)
    if not event:
        return {'ok': True, 'stored': False, 'reason': 'no_event'}
    result = ingest_event(event, picture)
    logger.info('Attendance event stored=%s reason=%s matched=%s first=%s',
                result.get('stored'), result.get('reason', ''), result.get('matched'), result.get('first_entry'))
    return {'ok': True, **result}
