import io
from datetime import date
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .attendance import ingest_event
from .models import AccessCard, Attendance, AttendanceAlert, FaceEnrollment, FaceReview, User

SATURDAY, SUNDAY = '2026-10-24', '2026-10-25'


def swipe(time):
    return {'major': 5, 'minor': 1, 'time': time, 'cardNo': '1000000001', 'employeeNoString': '', 'name': 'X',
            'doorNo': 1}


@override_settings(ATTENDANCE_DAY_START_HOUR=7, ATTENDANCE_LATE_HOUR=9, ATTENDANCE_DOOR_MAJOR=5,
                   ATTENDANCE_HIDDEN_DAYS={5, 6}, ATTENDANCE_EXEMPT_ROLES=['chair'], TELEGRAM_BOT_TOKEN='')
class WeekendTimesHiddenTests(TestCase):
    def setUp(self):
        self.employee = User.objects.create_user('xodim', full_name='Sinov Xodim', role='employee',
                                                 telegram_id=5001, password='x')
        self.secretary = User.objects.create_user('kotib', full_name='Sinov Kotib', role='secretary',
                                                  telegram_id=5002, password='x')
        self.root = User.objects.create_superuser('admin', password='Testing-123!')
        AccessCard.objects.create(user=self.employee, card_no='1000000001')
        FaceEnrollment.objects.create(user=self.employee, embedding=[0.1] * 512,
                                      photo=SimpleUploadedFile('f.jpg', b'f'))

    @patch('core.telegram.configured', return_value=True)
    @patch('core.telegram.message', return_value=True)
    @patch('core.face.available', return_value=True)
    @patch('core.face.best_match', return_value=0.1)
    def test_weekend_is_recorded_quietly(self, match, available, message, configured):
        picture = SimpleUploadedFile('c.jpg', b'c', content_type='image/jpeg')
        with self.captureOnCommitCallbacks(execute=True):
            result = ingest_event(swipe(f'{SUNDAY} 09:47:00'), picture)
        self.assertEqual(result['reason'], 'hidden_day')
        self.assertEqual(Attendance.objects.get(date=SUNDAY).status, 'present')
        match.assert_not_called()                     # no face check, so no review shows the time
        self.assertFalse(FaceReview.objects.exists())
        message.assert_not_called()
        self.assertFalse(AttendanceAlert.objects.exists())

    def test_secretary_sees_saturday_as_a_day_off_and_the_door_log_in_full(self):
        ingest_event(swipe(f'{SATURDAY} 09:47:00'))
        self.client.force_login(self.secretary)
        page = self.client.get(reverse('attendance_daily') + f'?date={SATURDAY}')
        self.assertEqual(str(page.context['day']), SATURDAY)
        self.assertIn(SATURDAY, [str(chip['date']) for chip in page.context['chips']])   # still offered
        self.assertEqual(page.context['rows'], [])
        self.assertContains(page, 'Dam olish kuni')
        self.assertNotContains(page, 'Sinov Xodim')
        self.assertNotContains(page, 'format=xlsx')
        events = self.client.get(reverse('access_events') + f'?from={SATURDAY}T00:00&to={SATURDAY}T23:59')
        self.assertEqual(len(events.context['events']), 1)

    def test_no_export_for_a_day_off(self):
        ingest_event(swipe(f'{SATURDAY} 09:47:00'))
        self.client.force_login(self.secretary)
        response = self.client.get(reverse('attendance_daily'), {'date': SATURDAY, 'format': 'xlsx'})
        self.assertNotIn('attachment', response.get('Content-Disposition', ''))

    def test_employee_month_shows_a_day_off(self):
        from .views import my_month_rows
        ingest_event(swipe(f'{SATURDAY} 09:47:00'))
        rows = {row['date']: row for row in my_month_rows(self.employee, date(2026, 10, 1), date(2026, 10, 25),
                                                          date(2026, 10, 20))}
        saturday = rows[date(2026, 10, 24)]
        self.assertEqual(saturday['state'], 'weekend')
        self.assertIsNone(saturday['attendance'])
        self.assertTrue(Attendance.objects.filter(user=self.employee, date=SATURDAY).exists())   # still kept

    def test_superuser_reads_the_times_in_the_admin(self):
        ingest_event(swipe(f'{SATURDAY} 09:47:00'))
        self.client.force_login(self.root)
        self.assertContains(self.client.get(reverse('admin:core_attendance_changelist')), '09:47')
