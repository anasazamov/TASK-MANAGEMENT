"""Attendance for the assistant: the same scope, rules and services as the attendance pages.

Reads return what the matching page would show this user. Changes are previewed in a
rolled-back transaction and saved only after the user confirms, like every other
assistant action. Face reviews and face photos are left to their pages: deciding one
means looking at a picture.
"""
import hashlib
import json
from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from ..attendance import service as service
from ..models import AgentProposal, Attendance, AttendanceExcuse, ExcuseRequest
from ..services import require

READ = ('attendance_month', 'attendance_day', 'list_excuse_requests')
WRITE = ('prepare_excuse_request', 'prepare_excuse_cancel', 'prepare_excuse_decision',
         'prepare_excuse_entry', 'prepare_excuse_remove')
PAGES = {'my_attendance': 'Mening davomatim', 'my_excuse_request': 'Ruxsat so‘rash',
         'attendance_daily': 'Kunlik davomat', 'excuse_requests': 'Ruxsat so‘rovlari',
         'access_events': 'Eshik ochilishlari', 'face_reviews': 'Yuz tekshiruvi',
         'face_enrollments': 'Yuzlarni biriktirish'}
STATES = {'present': 'Keldi', 'late': 'Kechikdi', 'review': 'Tekshiruvda', 'rejected': 'Rad etildi',
          'excused': 'Uzrli', 'absent': 'Kelmagan', 'not_yet': 'Hali kelmagan', 'weekend': 'Dam olish kuni'}
MAX_ROWS = 60


def parse_day(value, fallback=None):
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError('Sana noto‘g‘ri. Kun, oy va yilni aniq ayting.')


def period(start, end):
    return f'{start:%d.%m.%Y}' if not end or end == start else f'{start:%d.%m.%Y} – {end:%d.%m.%Y}'


def clock(moment):
    return timezone.localtime(moment).strftime('%H:%M') if moment else None


def employee(user, pk):
    """A tracked employee whose attendance this user may see and manage."""
    require(user.sees_employees)
    person = service.tracked(service.visible_employees(user)).filter(pk=pk).first()
    if not person:
        raise ValidationError('Xodim topilmadi yoki uning davomatini ko‘rish huquqingiz yo‘q.')
    return person


def open_page(user, page, day=None):
    if page == 'attendance_daily' and not user.sees_employees:
        page = 'my_attendance'          # an employee's "davomat" is their own
    if page in ('attendance_daily', 'access_events'):
        require(user.sees_employees)
    if page in ('excuse_requests', 'face_reviews', 'face_enrollments'):
        require(user.can_manage_attendance)
    url, label = reverse(page), PAGES[page]
    if day and page in ('attendance_daily', 'my_excuse_request'):
        url += f'?date={day:%Y-%m-%d}'
        label += f' — {day:%d.%m.%Y}'
    elif day and page == 'my_attendance':
        url += f'?month={day:%Y-%m}'
    return {'navigation': {'url': url, 'label': label}, 'message': label + ' sahifasini ochyapman.'}


def month(user, args):
    from ..views import UZ_MONTHS, my_month_rows
    own = args.employee_id in (None, user.pk)
    person = user if own else employee(user, args.employee_id)
    today = timezone.localdate()
    exempt = service.is_exempt(person)
    since = None if exempt else service.tracking_start(person)
    first = parse_day(args.month + '-01' if args.month else None, today.replace(day=1))
    first = max(min(first, today.replace(day=1)), (since or today).replace(day=1))
    rows = my_month_rows(person, first, today, since)
    days = [{'date': f"{row['date']:%d.%m.%Y}", 'weekday': row['weekday'], 'state': row['state'],
             'first_entry': clock(row['attendance'].first_entry_at) if row['attendance'] else None,
             'late': row['late_label'],
             'note': row['excuse'].label if row['excuse'] else ('So‘rov ko‘rib chiqilmoqda' if row['pending'] else '')}
            for row in rows if row['state'] != 'weekend']
    return {'employee': person.full_name, 'own': own, 'month': f'{UZ_MONTHS[first.month - 1]} {first.year}',
            'exempt': exempt, 'tracked': since is not None,
            'counts': {key: sum(1 for row in rows if row['state'] == key)
                       for key in ['present', 'late', 'review', 'rejected', 'excused', 'absent']},
            'days': days}


def day_state(row, day, today):
    attendance = row['attendance']
    if attendance and attendance.status != Attendance.Status.PRESENT:
        return attendance.status
    if attendance:
        return 'late' if row['late'] else 'present'
    if row['excuse']:
        return 'excused'
    if day == today and timezone.localtime().hour < settings.ATTENDANCE_ABSENT_HOUR:
        return 'not_yet'
    return 'absent'


def day(user, args):
    from ..views import attendance_day_rows
    require(user.sees_employees)
    today = timezone.localdate()
    chosen = parse_day(args.date, today)
    if chosen > today:
        raise ValidationError('Kelajakdagi kun uchun davomat hali yo‘q.')
    rows, day_off = attendance_day_rows(user, chosen)
    people = [{'id': row['user'].pk, 'name': row['user'].full_name,
               'department': row['user'].department.name if row['user'].department else '',
               'state': day_state(row, chosen, today),
               'first_entry': clock(row['attendance'].first_entry_at) if row['attendance'] else None,
               'late': row['late_label'], 'note': row['excuse'].label if row['excuse'] else ''} for row in rows]
    counts = {key: sum(1 for p in people if p['state'] == key) for key in
              ['present', 'late', 'review', 'rejected', 'excused', 'absent', 'not_yet']}
    wanted = {'all': set(STATES), 'present': {'present', 'late'}, 'absent': {'absent', 'not_yet'}}.get(args.only, {args.only})
    shown = [p for p in people if p['state'] in wanted]
    return {'date': f'{chosen:%d.%m.%Y}', 'day_off': day_off, 'total': len(people), 'counts': counts,
            'only': args.only, 'matching': len(shown), 'employees': shown[:MAX_ROWS]}


def request_row(item):
    return {'id': item.pk, 'employee': item.user.full_name, 'days': item.period_label, 'reason': item.label,
            'state': item.get_state_display(), 'pending': item.state == ExcuseRequest.State.PENDING,
            'decision_note': item.reason}


def excuse_requests(user, args):
    if args.scope == 'mine':
        items = user.excuse_requests.select_related('user')[:20]
    else:
        require(user.can_manage_attendance)
        items = (ExcuseRequest.objects.filter(user__in=service.visible_employees(user), state=ExcuseRequest.State.PENDING)
                 .select_related('user').order_by('start', 'created_at')[:40])
    return {'scope': args.scope, 'requests': [request_row(item) for item in items]}


def read(user, name, args):
    require(user.is_active)
    return {'attendance_month': month, 'attendance_day': day, 'list_excuse_requests': excuse_requests}[name](user, args)


def numbered(items):
    return '\n'.join(f'{i}. {item}' for i, item in enumerate(items, 1))


def person_line(row):
    text = row['name'] + (f" ({row['department']})" if row['department'] else '') + ' — ' + STATES[row['state']]
    if row['first_entry']:
        text += f", {row['first_entry']} da kirgan"
    if row['late']:
        text += f", {row['late']} kechikkan"
    return text + (f". {row['note']}" if row['note'] else '')


def day_line(row):
    text = f"{row['date']} ({row['weekday']}) — {STATES[row['state']]}"
    if row['first_entry']:
        text += f", {row['first_entry']} da"
    if row['late']:
        text += f", {row['late']} kechikish"
    return text + (f". {row['note']}" if row['note'] else '')


def tally(counts, keys):
    return ', '.join(f'{STATES[key].lower()}: {counts[key]}' for key in keys if counts.get(key))


def render(name, data, focus='overview'):
    if name == 'attendance_month':
        who = 'Sizning davomatingiz' if data['own'] else data['employee'] + ' davomati'
        if data['exempt']:
            return who + ' yuritilmaydi: rahbariyat davomatdan ozod.'
        if not data['tracked']:
            return who + ' hali yuritilmayapti: domofon kartasi hisobga bog‘lanmagan.'
        counts = data['counts']
        came = counts['present'] + counts['late']
        head = (f"{who}, {data['month']}: kelgan kunlar — {came}, shundan kechikkan — {counts['late']}; "
                f"uzrli — {counts['excused']}; kelmagan — {counts['absent']}")
        head += ''.join(f'; {label} — {counts[key]}' for key, label in
                        [('review', 'tekshiruvda'), ('rejected', 'rad etilgan')] if counts[key]) + '.'
        if focus == 'count' or not data['days']:
            return head
        return head + '\n' + numbered([day_line(row) for row in data['days']])
    if name == 'attendance_day':
        if data['day_off']:
            return f"{data['date']} — dam olish kuni, davomat yuritilmaydi."
        counts = data['counts']
        came = counts['present'] + counts['late']
        head = (f"{data['date']} davomati, {data['total']} xodim: kelgan — {came}, shundan kechikkan — {counts['late']}; "
                f"uzrli — {counts['excused']}; kelmagan — {counts['absent']}")
        head += ''.join(f'; {label} — {counts[key]}' for key, label in
                        [('not_yet', 'hali kelmagan'), ('review', 'tekshiruvda'), ('rejected', 'rad etilgan')] if counts[key]) + '.'
        if focus == 'count':
            return head
        if not data['employees']:
            return head + ('' if data['only'] == 'all' else ' So‘ralgan holatda xodim yo‘q.')
        more = '\n…' if data['matching'] > len(data['employees']) else ''
        return head + '\n' + numbered([person_line(row) for row in data['employees']]) + more
    if name == 'list_excuse_requests':
        mine = data['scope'] == 'mine'
        if not data['requests']:
            return 'Sizda hali ruxsat so‘rovi yo‘q.' if mine else 'Ko‘rib chiqiladigan ruxsat so‘rovi yo‘q.'
        if focus == 'count':
            return f"{'So‘rovlaringiz' if mine else 'Ko‘rib chiqiladigan so‘rovlar'}: {len(data['requests'])} ta."
        rows = [(('' if mine else item['employee'] + ' — ') + f"{item['days']}, {item['reason']}"
                 + (f" ({item['state']})" if mine else '')
                 + (f". Qaror izohi: {item['decision_note']}" if mine and item['decision_note'] else ''))
                for item in data['requests']]
        return ('Sizning so‘rovlaringiz:\n' if mine else 'Ko‘rib chiqiladigan ruxsat so‘rovlari:\n') + numbered(rows)
    raise ValidationError('Javob uchun tekshirilgan ma’lumot topilmadi.')


def spans(args):
    start = parse_day(args.date)
    end = parse_day(args.until)
    try:
        expected = datetime.strptime(args.expected_at, '%H:%M').time() if args.expected_at else None
    except ValueError:
        raise ValidationError('Kelish vaqtini soat va daqiqa bilan ayting, masalan 10:30.')
    return start, end, expected


def kind_label(kind, expected, note):
    text = AttendanceExcuse.Kind(kind).label
    if kind == AttendanceExcuse.Kind.LATE and expected:
        text += f' ({expected:%H:%M} da)'
    return f'{text}: {note.strip()}' if note.strip() else text


def version(user, name, args):
    if name == 'prepare_excuse_request':
        data = list(user.excuse_requests.filter(state=ExcuseRequest.State.PENDING).values_list('pk', flat=True))
    elif name in ('prepare_excuse_cancel', 'prepare_excuse_decision'):
        data = list(ExcuseRequest.objects.filter(pk=args.request_id).values('id', 'state'))
    else:
        start = parse_day(args.date)
        end = parse_day(getattr(args, 'until', None)) or start
        data = list(AttendanceExcuse.objects.filter(user_id=args.employee_id, date__range=(start, end))
                    .values('id', 'date', 'kind', 'note', 'expected_at'))
    return hashlib.sha256(json.dumps(data, default=str, sort_keys=True).encode()).hexdigest()


def apply(user, name, args):
    """Do the change through the page's own service; returns (preview, result)."""
    require(user.is_active)
    if name == 'prepare_excuse_request':
        start, end, expected = spans(args)
        item = service.request_excuse(user, args.kind, start, end, args.note, expected)
        return ({'Amal': 'Sababini bildirish' if start < timezone.localdate() else 'Ruxsat so‘rash',
                 'Kunlar': item.period_label, 'Sabab': item.label, 'Kim ko‘rib chiqadi': 'Kotib referent'},
                {'message': 'So‘rovingiz kotib referentga yuborildi. Qaror Telegram orqali keladi.',
                 'navigation': {'url': f"{reverse('my_attendance')}?month={start:%Y-%m}", 'label': 'Mening davomatim'}})
    if name == 'prepare_excuse_cancel':
        item = user.excuse_requests.filter(pk=args.request_id).first()
        if not item:
            raise ValidationError('Bunday so‘rovingiz topilmadi.')
        service.cancel_excuse_request(user, item.pk)
        return ({'Amal': 'Ruxsat so‘rovini bekor qilish', 'Kunlar': item.period_label, 'Sabab': item.label},
                {'message': 'So‘rov bekor qilindi.',
                 'navigation': {'url': reverse('my_attendance'), 'label': 'Mening davomatim'}})
    if name == 'prepare_excuse_decision':
        require(user.can_manage_attendance)
        item = (ExcuseRequest.objects.filter(user__in=service.visible_employees(user), pk=args.request_id)
                .select_related('user').first())
        if not item:
            raise ValidationError('So‘rov topilmadi yoki uni ko‘rib chiqish huquqingiz yo‘q.')
        service.resolve_excuse_request(user, item.pk, args.approve, args.reason)
        preview = {'Amal': 'Ruxsat so‘rovini tasdiqlash' if args.approve else 'Ruxsat so‘rovini rad etish',
                   'Xodim': item.user.full_name, 'Kunlar': item.period_label, 'Sabab': item.label}
        if args.reason.strip():
            preview['Qaror izohi'] = args.reason.strip()
        return preview, {'message': f'{item.user.full_name}: so‘rov ' + ('tasdiqlandi, uzrli sabab kiritildi.' if args.approve
                                                                        else 'rad etildi. Xodimga xabar yuborildi.'),
                         'navigation': {'url': reverse('excuse_requests'), 'label': 'Ruxsat so‘rovlari'}}
    person = employee(user, args.employee_id)
    if name == 'prepare_excuse_entry':
        start, end, expected = spans(args)
        days = service.set_excuse(user, person, args.kind, start, end, args.note, expected)
        return ({'Amal': 'Sabab kiritish', 'Xodim': person.full_name, 'Kunlar': period(start, end),
                 'Sabab': kind_label(args.kind, expected, args.note)},
                {'message': f'{person.full_name}: uzrli sabab {days} kunga kiritildi.',
                 'navigation': {'url': f"{reverse('attendance_daily')}?date={start:%Y-%m-%d}", 'label': 'Kunlik davomat'}})
    chosen = parse_day(args.date)
    excuse = AttendanceExcuse.objects.filter(user=person, date=chosen).first()
    if not excuse:
        raise ValidationError(f'{person.full_name} uchun {chosen:%d.%m.%Y} kuniga uzrli sabab kiritilmagan.')
    label = excuse.label
    excuse.delete()
    return ({'Amal': 'Uzrli sababni bekor qilish', 'Xodim': person.full_name, 'Kun': f'{chosen:%d.%m.%Y}', 'Sabab': label},
            {'message': f'{person.full_name}: uzrli sabab bekor qilindi.',
             'navigation': {'url': f"{reverse('attendance_daily')}?date={chosen:%Y-%m-%d}", 'label': 'Kunlik davomat'}})


def prepare(user, conversation, name, args):
    from .agent_tools import proposal_data
    stamp = version(user, name, args)
    with transaction.atomic():
        preview, _ = apply(user, name, args)
        transaction.set_rollback(True)
    AgentProposal.objects.filter(conversation=conversation, state='pending').update(state='cancelled')
    proposal = AgentProposal.objects.create(conversation=conversation, payload={'tool': name, 'args': args.model_dump()},
                                            preview=preview, snapshot=stamp, expires_at=timezone.now() + timedelta(minutes=10))
    return {'proposal': proposal_data(proposal),
            'message': 'Amal tayyor. Tekshirib, «tasdiqlayman» deng yoki tasdiqlash tugmasini bosing.'}


def confirm(user, proposal, name, args):
    # Called inside the proposal transaction; permissions and current data are checked again.
    if version(user, name, args) != proposal.snapshot:
        raise ValidationError('Ma’lumot o‘zgargan. Amalni yangidan tayyorlang.')
    _, result = apply(user, name, args)
    result['proposal'] = None
    proposal.state, proposal.result = 'completed', result
    proposal.save(update_fields=['state', 'result'])
    return result
