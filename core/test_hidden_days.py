from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .attendance import ingest_event
from .models import AccessCard, Attendance, AttendanceAlert, FaceEnrollment, FaceReview, User

SATURDAY, FRIDAY = '2026-10-24', '2026-10-23'


def swipe(time):
    return {'major': 5, 'minor': 1, 'time': time, 'cardNo': '1000000001', 'employeeNoString': '', 'name': 'X',
            'doorNo': 1}


@override_settings(ATTENDANCE_DAY_START_HOUR=7, ATTENDANCE_DOOR_MAJOR=5, ATTENDANCE_HIDDEN_DAYS={5},
                   ATTENDANCE_EXEMPT_ROLES=['chair'], TELEGRAM_BOT_TOKEN='')
class HiddenSaturdayTests(TestCase):
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
    def test_saturday_is_recorded_quietly(self, match, available, message, configured):
        picture = SimpleUploadedFile('c.jpg', b'c', content_type='image/jpeg')
        with self.captureOnCommitCallbacks(execute=True):
            result = ingest_event(swipe(f'{SATURDAY} 09:30:00'), picture)
        self.assertEqual(result['reason'], 'hidden_day')
        day = Attendance.objects.get(date=SATURDAY)
        self.assertEqual((day.entry_count, day.status), (1, 'present'))
        match.assert_not_called()                     # no face check, so no review to show
        self.assertFalse(FaceReview.objects.exists())
        message.assert_not_called()                   # nobody is told
        self.assertFalse(AttendanceAlert.objects.exists())

    def test_secretary_does_not_see_saturday_but_a_superuser_does(self):
        ingest_event(swipe(f'{SATURDAY} 09:30:00'))
        self.client.force_login(self.secretary)
        page = self.client.get(reverse('attendance_daily') + f'?date={SATURDAY}')
        self.assertEqual(str(page.context['day']), FRIDAY)     # stepped back to Friday
        self.assertNotIn(SATURDAY, [str(chip['date']) for chip in page.context['chips']])
        events = self.client.get(reverse('access_events') + f'?from={SATURDAY}T00:00&to={SATURDAY}T23:59')
        self.assertEqual(len(events.context['events']), 0)
        # The superuser reads it in the admin, where nothing is hidden.
        self.client.force_login(self.root)
        self.assertContains(self.client.get(reverse('admin:core_attendance_changelist')), 'Sinov X.')
        self.assertContains(self.client.get(reverse('admin:core_accessevent_changelist')), '1000000001')

    def test_employee_sees_saturday_as_a_day_off(self):
        from .views import my_month_rows
        from datetime import date
        ingest_event(swipe(f'{SATURDAY} 09:30:00'))
        rows = {row['date']: row for row in my_month_rows(self.employee, date(2026, 10, 1), date(2026, 10, 25),
                                                          date(2026, 10, 20))}
        saturday = rows[date(2026, 10, 24)]
        self.assertEqual(saturday['state'], 'weekend')
        self.assertIsNone(saturday['attendance'])
        self.assertTrue(Attendance.objects.filter(user=self.employee, date=SATURDAY).exists())   # still kept
