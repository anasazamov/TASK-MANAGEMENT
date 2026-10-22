from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .attendance import cancel_excuse_request, request_excuse, resolve_excuse_request
from .models import Attendance, AttendanceExcuse, ExcuseRequest, User


@patch('core.telegram.configured', return_value=True)
@patch('core.telegram.message', return_value=True)
class ExcuseRequestFlowTests(TestCase):
    def setUp(self):
        self.secretary = User.objects.create_user('kotib', full_name='Kotib Referent', role='secretary',
                                                  telegram_id=1001, password='x')
        self.employee = User.objects.create_user('sinov', full_name='Sinov Xodim', role='employee',
                                                 telegram_id=1002, password='x')
        self.today = timezone.localdate()

    def recipients(self, message):
        return [call.args[0] for call in message.call_args_list]

    def test_request_alerts_the_secretary_and_waits(self, message, configured):
        with self.captureOnCommitCallbacks(execute=True):
            item = request_excuse(self.employee, 'sick', self.today, self.today + timedelta(days=2), 'Shamollash')
        self.assertEqual(item.state, ExcuseRequest.State.PENDING)
        self.assertEqual(self.recipients(message), [self.secretary])
        self.assertIn('Uzr so‘rovi: Sinov Xodim', message.call_args.args[1])
        self.assertFalse(AttendanceExcuse.objects.exists())     # nothing is excused before a decision

    def test_approval_excuses_every_day_and_tells_the_employee(self, message, configured):
        item = request_excuse(self.employee, 'sick', self.today, self.today + timedelta(days=2))
        message.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            resolve_excuse_request(self.secretary, item.pk, True)
        excuses = AttendanceExcuse.objects.filter(user=self.employee)
        self.assertEqual(excuses.count(), 3)
        self.assertTrue(all(e.request_id == item.pk and e.created_by == self.secretary for e in excuses))
        self.assertEqual(self.recipients(message), [self.employee])
        self.assertIn('tasdiqlandi', message.call_args.args[1])

    def test_rejection_needs_a_reason_and_sends_it(self, message, configured):
        item = request_excuse(self.employee, 'late', self.today, note='Shifokorda', expected_at=None)
        with self.assertRaises(ValidationError):
            resolve_excuse_request(self.secretary, item.pk, False, '')
        with self.captureOnCommitCallbacks(execute=True):
            resolve_excuse_request(self.secretary, item.pk, False, 'Ma’lumotnoma yo‘q')
        item.refresh_from_db()
        self.assertEqual((item.state, item.reason), (ExcuseRequest.State.REJECTED, 'Ma’lumotnoma yo‘q'))
        self.assertFalse(AttendanceExcuse.objects.exists())
        self.assertIn('Ma’lumotnoma yo‘q', message.call_args.args[1])

    def test_a_decided_request_cannot_be_decided_again(self, message, configured):
        item = request_excuse(self.employee, 'sick', self.today)
        resolve_excuse_request(self.secretary, item.pk, True)
        with self.assertRaises(ValidationError):
            resolve_excuse_request(self.secretary, item.pk, False, 'kech')

    def test_rules(self, message, configured):
        with self.assertRaises(ValidationError):
            request_excuse(self.employee, 'other', self.today, note='')              # other needs a note
        with self.assertRaises(ValidationError):
            request_excuse(self.employee, 'sick', self.today - timedelta(days=40))  # too far back
        request_excuse(self.employee, 'sick', self.today, self.today + timedelta(days=3))
        with self.assertRaises(ValidationError):
            request_excuse(self.employee, 'trip', self.today + timedelta(days=1))   # overlaps a pending one

    def test_employee_cancels_only_their_own_pending_request(self, message, configured):
        item = request_excuse(self.employee, 'sick', self.today)
        with self.assertRaises(ValidationError):
            cancel_excuse_request(self.secretary, item.pk)
        cancel_excuse_request(self.employee, item.pk)
        item.refresh_from_db()
        self.assertEqual(item.state, ExcuseRequest.State.CANCELLED)
        with self.assertRaises(ValidationError):
            resolve_excuse_request(self.secretary, item.pk, True)


@override_settings(ATTENDANCE_LATE_HOUR=9, ATTENDANCE_ABSENT_HOUR=11)
class ExcusePagesTests(TestCase):
    def setUp(self):
        self.secretary = User.objects.create_user('kotib', full_name='Kotib Referent', role='secretary', password='x')
        self.employee = User.objects.create_user('sinov', full_name='Sinov Xodim', role='employee', password='x')
        self.today = timezone.localdate()

    def test_employee_sends_a_request_from_their_page(self):
        self.client.force_login(self.employee)
        page = self.client.get(reverse('my_attendance'))
        self.assertContains(page, 'Mening davomatim')
        self.assertContains(page, 'Uzrli sabab so‘rash')
        self.client.post(reverse('my_excuse_request'), {'kind': 'sick', 'date': self.today.isoformat(), 'note': 'Isitma'})
        self.assertEqual(ExcuseRequest.objects.get().user, self.employee)
        self.assertContains(self.client.get(reverse('my_attendance')), 'Ko‘rib chiqilmoqda')

    def test_month_rows_show_arrival_lateness_and_excuse(self):
        first = self.today.replace(day=1)
        start = timezone.make_aware(timezone.datetime.combine(first, timezone.datetime.min.time()))
        Attendance.objects.create(user=self.employee, date=first, entry_count=1,
                                  first_entry_at=start + timedelta(hours=9, minutes=25),
                                  last_entry_at=start + timedelta(hours=17))
        if first != self.today:
            AttendanceExcuse.objects.create(user=self.employee, date=self.today, kind='sick')
        self.client.force_login(self.employee)
        page = self.client.get(reverse('my_attendance'))
        self.assertContains(page, '+25 daq')
        if first != self.today:
            self.assertContains(page, 'Uzrli')

    def test_only_overseers_see_the_request_queue(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(reverse('excuse_requests')).status_code, 403)
        self.client.force_login(self.secretary)
        self.assertEqual(self.client.get(reverse('excuse_requests')).status_code, 200)

    def test_secretary_decides_on_the_page(self):
        item = request_excuse(self.employee, 'sick', self.today)
        self.client.force_login(self.secretary)
        self.assertContains(self.client.get(reverse('excuse_requests')), 'Sinov Xodim')
        self.client.post(reverse('excuse_request_action', args=[item.pk]), {'decision': 'approve'})
        self.assertTrue(AttendanceExcuse.objects.filter(user=self.employee, date=self.today).exists())

    def test_employee_cannot_decide(self):
        item = request_excuse(self.employee, 'sick', self.today)
        self.client.force_login(self.employee)
        response = self.client.post(reverse('excuse_request_action', args=[item.pk]), {'decision': 'approve'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AttendanceExcuse.objects.exists())


class MyAttendanceExperienceTests(TestCase):
    """What the employee sees: no false absences, and a way to ask right from the day."""

    def setUp(self):
        self.employee = User.objects.create_user('sinov', full_name='Sinov Xodim', role='employee', password='x')
        self.client.force_login(self.employee)
        self.today = timezone.localdate()

    def test_days_before_tracking_are_not_absences(self):
        page = self.client.get(reverse('my_attendance'))
        self.assertContains(page, 'Davomatingiz hali yuritilmayapti')
        self.assertContains(page, 'Kelmagan: <strong>0</strong>')
        self.assertNotContains(page, 'Uzr so‘rash</a>')

    def test_tracking_starts_with_the_card_and_older_months_are_closed(self):
        from .models import AccessCard
        card = AccessCard.objects.create(user=self.employee, card_no='1000000001')
        AccessCard.objects.filter(pk=card.pk).update(created_at=timezone.now() - timedelta(days=3))
        page = self.client.get(reverse('my_attendance') + '?month=2020-01')
        self.assertEqual(page.context['month'], self.today.replace(day=1))   # clamped to the first tracked month
        self.assertIsNone(page.context['previous_month'])
        self.assertNotContains(page, 'Davomatingiz hali yuritilmayapti')

    def test_a_missed_weekday_offers_a_prefilled_request(self):
        from .models import AccessCard
        card = AccessCard.objects.create(user=self.employee, card_no='1000000001')
        AccessCard.objects.filter(pk=card.pk).update(created_at=timezone.now() - timedelta(days=10))
        page = self.client.get(reverse('my_attendance'))
        absent = [row for row in page.context['rows'] if row['state'] == 'absent']
        if absent:                                     # the month may hold no past weekday yet
            self.assertTrue(absent[0]['can_request'])
            self.assertContains(page, f"?date={absent[0]['date']:%Y-%m-%d}")

    def test_form_is_prefilled_and_shows_errors_in_place(self):
        url = reverse('my_excuse_request')
        page = self.client.get(url + f'?date={self.today:%Y-%m-%d}&kind=late')
        self.assertContains(page, 'value="late" required checked')
        page = self.client.post(url, {'kind': 'other', 'date': self.today.isoformat(), 'note': ''})
        self.assertContains(page, 'So‘rov yuborilmadi')
        self.assertContains(page, 'izoh yozing')
        self.assertFalse(ExcuseRequest.objects.exists())

    def test_until_is_ignored_unless_several_days_are_ticked(self):
        url = reverse('my_excuse_request')
        later = (self.today + timedelta(days=3)).isoformat()
        self.client.post(url, {'kind': 'sick', 'date': self.today.isoformat(), 'until': later})
        self.assertEqual(ExcuseRequest.objects.get().end, self.today)
        ExcuseRequest.objects.all().delete()
        self.client.post(url, {'kind': 'sick', 'date': self.today.isoformat(), 'until': later, 'several': '1'})
        self.assertEqual(ExcuseRequest.objects.get().end, self.today + timedelta(days=3))
