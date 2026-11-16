from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from ..assistant import agent, agent_replies, agent_tools as tools
from ..models import AccessCard, AgentConversation, Attendance, AttendanceExcuse, Department, ExcuseRequest, User

MONDAY, SATURDAY = date(2026, 10, 19), date(2026, 10, 17)
NOW = timezone.make_aware(datetime(2026, 10, 21, 15, 0))        # Wednesday afternoon


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


@override_settings(ATTENDANCE_DAY_START_HOUR=7, ATTENDANCE_LATE_HOUR=9, ATTENDANCE_ABSENT_HOUR=11,
                   ATTENDANCE_HIDDEN_DAYS={5, 6}, ATTENDANCE_WEEKEND_DAYS={5, 6}, ATTENDANCE_EXEMPT_ROLES=['chair'],
                   TELEGRAM_BOT_TOKEN='')
class AgentAttendanceTests(TestCase):
    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.finance = Department.objects.create(name='Moliya')
        self.legal = Department.objects.create(name='Yuridik')
        make = User.objects.create_user
        self.chair = make('rais', full_name='Sinov Rais', role='chair')
        self.secretary = make('kotib', full_name='Sinov Kotib', role='secretary')
        self.head = make('boshliq', full_name='Sinov Boshliq', role='head', department=self.finance)
        self.worker = make('xodim', full_name='Sinov Xodim', role='employee', department=self.finance)
        self.lawyer = make('yurist', full_name='Sinov Yurist', role='employee', department=self.legal)
        self.hr = make('kadr', full_name='Sinov Kadr', role='employee', department=self.legal, is_hr=True)
        for index, person in enumerate([self.worker, self.lawyer, self.head]):
            AccessCard.objects.create(user=person, card_no=f'100000000{index}', created_at=at(MONDAY, 8))
        Attendance.objects.create(user=self.worker, date=MONDAY, first_entry_at=at(MONDAY, 9, 40),
                                  last_entry_at=at(MONDAY, 9, 40), entry_count=1)
        Attendance.objects.create(user=self.lawyer, date=MONDAY, first_entry_at=at(MONDAY, 8, 30),
                                  last_entry_at=at(MONDAY, 8, 30), entry_count=1)
        Attendance.objects.create(user=self.worker, date=SATURDAY, first_entry_at=at(SATURDAY, 10),
                                  last_entry_at=at(SATURDAY, 10), entry_count=1)

    def conversation(self, user):
        return AgentConversation.objects.create(user=user)

    def read(self, user, name, **args):
        return tools.execute(user, self.conversation(user), name, args, set())

    def propose(self, user, name, conversation=None, **args):
        conversation = conversation or self.conversation(user)
        result = tools.execute(user, conversation, name, args, set())
        return conversation, result['proposal']

    def ask(self, **changes):
        return {'kind': 'sick', 'date': '2026-10-22', 'until': None, 'expected_at': None, 'note': '', **changes}

    # Reading

    def test_employee_reads_own_month(self):
        data = self.read(self.worker, 'attendance_month', employee_id=None, month='2026-10')
        self.assertTrue(data['own'])
        self.assertEqual(data['counts']['late'], 1)
        monday = next(row for row in data['days'] if row['date'] == '19.10.2026')
        self.assertEqual((monday['state'], monday['first_entry'], monday['late']), ('late', '09:40', '40 daq'))
        text = agent_replies.render('attendance_month', data)
        self.assertIn('Sizning davomatingiz, Oktabr 2026', text)
        self.assertIn('19.10.2026 (Du) — Kechikdi, 09:40 da, 40 daq kechikish', text)

    def test_employee_cannot_read_others_or_the_day_list(self):
        with self.assertRaises(PermissionDenied):
            self.read(self.worker, 'attendance_month', employee_id=self.lawyer.pk, month=None)
        with self.assertRaises(PermissionDenied):
            self.read(self.worker, 'attendance_day', date='2026-10-19', only='all')
        with self.assertRaises(PermissionDenied):
            self.read(self.worker, 'list_excuse_requests', scope='to_decide')

    def test_head_sees_only_own_department(self):
        data = self.read(self.head, 'attendance_day', date='2026-10-19', only='all')
        self.assertEqual({row['name'] for row in data['employees']}, {'Sinov Boshliq', 'Sinov Xodim'})
        with self.assertRaises(ValidationError):
            self.read(self.head, 'attendance_month', employee_id=self.lawyer.pk, month=None)
        self.assertEqual(self.read(self.head, 'attendance_month', employee_id=self.worker.pk, month='2026-10')['employee'],
                         'Sinov Xodim')

    def test_secretary_asks_who_was_absent_and_late(self):
        absent = self.read(self.secretary, 'attendance_day', date='2026-10-19', only='absent')
        self.assertNotIn('Sinov Rais', str(absent))                       # exempt leadership is not tracked
        self.assertEqual({row['name'] for row in absent['employees']}, {'Sinov Boshliq', 'Sinov Kadr', 'Sinov Kotib'})
        late = self.read(self.secretary, 'attendance_day', date='2026-10-19', only='late')
        self.assertEqual([row['name'] for row in late['employees']], ['Sinov Xodim'])
        text = agent_replies.render('attendance_day', late)
        self.assertIn('kelgan — 2, shundan kechikkan — 1', text)
        self.assertIn('Sinov Xodim (Moliya) — Kechikdi, 09:40 da kirgan, 40 daq kechikkan', text)
        self.assertNotIn('\n', agent_replies.render('attendance_day', late, 'count'))

    def test_today_before_the_cutoff_is_not_yet_absent(self):
        with patch('django.utils.timezone.now', return_value=at(date(2026, 10, 21), 9, 30)):
            data = self.read(self.secretary, 'attendance_day', date=None, only='absent')
        self.assertEqual({row['state'] for row in data['employees']}, {'not_yet'})

    def test_weekend_entries_stay_hidden(self):
        data = self.read(self.secretary, 'attendance_day', date='2026-10-17', only='all')
        self.assertTrue(data['day_off'])
        self.assertEqual(data['employees'], [])
        self.assertIn('dam olish kuni', agent_replies.render('attendance_day', data))
        month = self.read(self.worker, 'attendance_month', employee_id=None, month='2026-10')
        self.assertNotIn('17.10.2026', str(month))
        self.assertNotIn('10:00', str(month))

    def test_exempt_and_future(self):
        data = self.read(self.chair, 'attendance_month', employee_id=None, month=None)
        self.assertIn('yuritilmaydi', agent_replies.render('attendance_month', data))
        with self.assertRaises(ValidationError):
            self.read(self.secretary, 'attendance_day', date='2026-10-22', only='all')
        with self.assertRaises(ValidationError):
            self.read(self.secretary, 'attendance_day', date='ertaga', only='all')

    # The employee's own request

    @patch('core.notifications.telegram.configured', return_value=True)
    @patch('core.notifications.telegram.message', return_value=True)
    def test_request_is_previewed_then_sent_once(self, message, configured):
        with self.captureOnCommitCallbacks(execute=True):
            conversation, proposal = self.propose(self.worker, 'prepare_excuse_request', **self.ask())
        self.assertFalse(ExcuseRequest.objects.exists())
        message.assert_not_called()                                       # a preview tells nobody
        self.assertEqual(proposal['preview'], {'Amal': 'Ruxsat so‘rash', 'Kunlar': '22.10.2026', 'Sabab': 'Kasal',
                                               'Kim ko‘rib chiqadi': 'Kotib referent'})
        with self.captureOnCommitCallbacks(execute=True):
            result = tools.confirm(self.worker, conversation, proposal['id'])
        item = ExcuseRequest.objects.get()
        self.assertEqual((item.user, item.kind, item.start, item.state), (self.worker, 'sick', date(2026, 10, 22), 'pending'))
        self.assertEqual(result['navigation']['url'], '/me/attendance/?month=2026-10')
        self.assertEqual({call.args[0] for call in message.call_args_list}, {self.secretary, self.hr})
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(tools.confirm(self.worker, conversation, proposal['id']), result)
        self.assertEqual(ExcuseRequest.objects.count(), 1)
        self.assertEqual(message.call_count, 2)

    def test_request_rules_are_the_forms_rules(self):
        _, proposal = self.propose(self.worker, 'prepare_excuse_request',
                                   **self.ask(kind='late', date='2026-10-19', expected_at='10:30'))
        self.assertEqual((proposal['preview']['Amal'], proposal['preview']['Sabab']),
                         ('Sababini bildirish', 'Kechroq keladi (10:30 da)'))
        for bad in [self.ask(kind='other'), self.ask(date='2026-08-01'), self.ask(until='2026-10-20'),
                    self.ask(date='22.10.2026'), self.ask(kind='late', expected_at='11 da')]:
            with self.assertRaises(ValidationError):
                self.propose(self.worker, 'prepare_excuse_request', **bad)

    def test_changed_requests_make_the_preview_stale(self):
        conversation, proposal = self.propose(self.worker, 'prepare_excuse_request', **self.ask())
        ExcuseRequest.objects.create(user=self.worker, kind='trip', start=date(2026, 10, 28), end=date(2026, 10, 28))
        with self.assertRaises(ValidationError):
            tools.confirm(self.worker, conversation, proposal['id'])

    def test_own_requests_are_listed_and_cancelled(self):
        mine = ExcuseRequest.objects.create(user=self.worker, kind='sick', start=date(2026, 10, 22), end=date(2026, 10, 23))
        other = ExcuseRequest.objects.create(user=self.lawyer, kind='trip', start=date(2026, 10, 22), end=date(2026, 10, 22))
        data = self.read(self.worker, 'list_excuse_requests', scope='mine')
        self.assertEqual([row['id'] for row in data['requests']], [mine.pk])
        self.assertIn('22.10.2026 – 23.10.2026, Kasal (Ko‘rib chiqilmoqda)', agent_replies.render('list_excuse_requests', data))
        with self.assertRaises(ValidationError):
            self.propose(self.worker, 'prepare_excuse_cancel', request_id=other.pk)
        conversation, proposal = self.propose(self.worker, 'prepare_excuse_cancel', request_id=mine.pk)
        mine.refresh_from_db()
        self.assertEqual(mine.state, 'pending')
        tools.confirm(self.worker, conversation, proposal['id'])
        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((mine.state, other.state), ('cancelled', 'pending'))

    # Deciding

    @patch('core.notifications.telegram.configured', return_value=True)
    @patch('core.notifications.telegram.message', return_value=True)
    def test_hr_approves_and_the_days_become_excused(self, message, configured):
        item = ExcuseRequest.objects.create(user=self.worker, kind='sick', start=date(2026, 10, 22), end=date(2026, 10, 23))
        data = self.read(self.hr, 'list_excuse_requests', scope='to_decide')
        self.assertEqual([(row['id'], row['employee']) for row in data['requests']], [(item.pk, 'Sinov Xodim')])
        with self.captureOnCommitCallbacks(execute=True):
            conversation, proposal = self.propose(self.hr, 'prepare_excuse_decision', request_id=item.pk, approve=True, reason='')
        self.assertFalse(AttendanceExcuse.objects.exists())
        message.assert_not_called()
        with self.captureOnCommitCallbacks(execute=True):
            tools.confirm(self.hr, conversation, proposal['id'])
        item.refresh_from_db()
        self.assertEqual((item.state, item.resolved_by), ('approved', self.hr))
        self.assertEqual(AttendanceExcuse.objects.filter(user=self.worker, request=item).count(), 2)
        self.assertEqual([call.args[0] for call in message.call_args_list], [self.worker])

    def test_refusal_needs_a_reason_and_the_right_role(self):
        item = ExcuseRequest.objects.create(user=self.worker, kind='leave', start=date(2026, 10, 22), end=date(2026, 10, 22))
        with self.assertRaises(ValidationError):
            self.propose(self.secretary, 'prepare_excuse_decision', request_id=item.pk, approve=False, reason=' ')
        for outsider in (self.worker, self.head):
            with self.assertRaises(PermissionDenied):
                self.propose(outsider, 'prepare_excuse_decision', request_id=item.pk, approve=True, reason='')
        conversation, proposal = self.propose(self.secretary, 'prepare_excuse_decision', request_id=item.pk,
                                              approve=False, reason='Hisobot kuni')
        self.assertEqual(proposal['preview']['Qaror izohi'], 'Hisobot kuni')
        # Someone else decides first: the prepared refusal must not overwrite it.
        ExcuseRequest.objects.filter(pk=item.pk).update(state='approved')
        with self.assertRaises(ValidationError):
            tools.confirm(self.secretary, conversation, proposal['id'])

    def test_role_lost_before_confirming(self):
        item = ExcuseRequest.objects.create(user=self.worker, kind='leave', start=date(2026, 10, 22), end=date(2026, 10, 22))
        conversation, proposal = self.propose(self.hr, 'prepare_excuse_decision', request_id=item.pk, approve=True, reason='')
        User.objects.filter(pk=self.hr.pk).update(is_hr=False)
        self.hr.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            tools.confirm(self.hr, conversation, proposal['id'])
        self.assertFalse(AttendanceExcuse.objects.exists())

    # Staff entering and removing a reason

    def test_head_enters_and_removes_a_reason_in_own_department_only(self):
        entry = dict(employee_id=self.worker.pk, kind='trip', date='2026-10-20', until='2026-10-21', expected_at=None,
                     note='Toshkent')
        conversation, proposal = self.propose(self.head, 'prepare_excuse_entry', **entry)
        self.assertEqual(proposal['preview'], {'Amal': 'Sabab kiritish', 'Xodim': 'Sinov Xodim',
                                               'Kunlar': '20.10.2026 – 21.10.2026', 'Sabab': 'Xizmat safari: Toshkent'})
        self.assertFalse(AttendanceExcuse.objects.exists())
        tools.confirm(self.head, conversation, proposal['id'])
        self.assertEqual(AttendanceExcuse.objects.filter(user=self.worker, created_by=self.head).count(), 2)
        with self.assertRaises(ValidationError):
            self.propose(self.head, 'prepare_excuse_entry', **{**entry, 'employee_id': self.lawyer.pk})
        with self.assertRaises(PermissionDenied):
            self.propose(self.worker, 'prepare_excuse_entry', **entry)
        with self.assertRaises(ValidationError):
            self.propose(self.head, 'prepare_excuse_remove', employee_id=self.worker.pk, date='2026-10-19')
        conversation, proposal = self.propose(self.head, 'prepare_excuse_remove', employee_id=self.worker.pk, date='2026-10-20')
        self.assertEqual(AttendanceExcuse.objects.count(), 2)
        tools.confirm(self.head, conversation, proposal['id'])
        self.assertEqual([e.date for e in AttendanceExcuse.objects.all()], [date(2026, 10, 21)])

    # Opening pages, and naming people

    def test_pages_open_by_role(self):
        self.assertEqual(agent.shortcut(self.worker, 'Davomatni och')['navigation']['url'], '/me/attendance/')
        self.assertEqual(agent.shortcut(self.secretary, 'Davomatni och')['navigation']['url'], '/employees/attendance/')
        self.assertEqual(agent.shortcut(self.worker, 'mening davomatimni ko‘rsat')['navigation']['url'], '/me/attendance/')
        self.assertEqual(agent.shortcut(self.hr, 'ruxsat so‘rovlarini och')['navigation']['url'], '/employees/excuses/')
        with self.assertRaises(PermissionDenied):
            agent.shortcut(self.worker, 'ruxsat so‘rovlarini och')
        opened = self.read(self.secretary, 'open_attendance', page='attendance_daily', date='2026-10-19')
        self.assertEqual(opened['navigation']['url'], '/employees/attendance/?date=2026-10-19')
        for page in ('face_reviews', 'face_enrollments', 'access_events'):
            with self.assertRaises(PermissionDenied):
                self.read(self.worker, 'open_attendance', page=page, date=None)
        self.assertEqual(self.read(self.worker, 'open_attendance', page='my_excuse_request', date='2026-10-22')['navigation']['url'],
                         '/me/attendance/excuse/?date=2026-10-22')

    def test_hr_can_name_any_employee(self):
        found = self.read(self.hr, 'list_people', query='Xodim')
        self.assertEqual([person['name'] for person in found['people']], ['Sinov Xodim'])
        self.assertEqual(self.read(self.lawyer, 'list_people', query='Xodim')['people'], [])

    def test_every_attendance_tool_is_offered_to_the_model(self):
        names = {schema['name'] for schema in tools.schemas()}
        self.assertLessEqual({'attendance_month', 'attendance_day', 'list_excuse_requests', 'open_attendance',
                              *tools.ATTENDANCE_TOOLS}, names)
        self.assertIn('prepare_excuse_request', agent.INSTRUCTIONS)
