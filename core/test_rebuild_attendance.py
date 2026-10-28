import io
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from .attendance import ingest_event
from .models import AccessCard, Attendance, User


def swipe(card, time):
    return {'major': 5, 'minor': 1, 'time': time, 'cardNo': card, 'employeeNoString': '', 'name': 'X', 'doorNo': 1}


@override_settings(ATTENDANCE_DAY_START_HOUR=7, ATTENDANCE_LATE_HOUR=9, ATTENDANCE_DOOR_MAJOR=5)
class RebuildAttendanceTests(TestCase):
    def setUp(self):
        self.deputy = User.objects.create_user('orinbosar', full_name='Sinov Orinbosar', role='deputy')
        AccessCard.objects.create(user=self.deputy, card_no='1000000001')
        # The deputy was exempt when these came in: logged, no attendance.
        with override_settings(ATTENDANCE_EXEMPT_ROLES=['chair', 'deputy']):
            for moment in ['2026-10-26 06:40:00', '2026-10-26 09:20:00', '2026-10-26 17:30:00', '2026-10-27 08:50:00']:
                ingest_event(swipe('1000000001', moment))
        self.assertFalse(Attendance.objects.exists())

    def run_command(self, *args):
        out = io.StringIO()
        call_command('rebuild_attendance', '--role', 'deputy', *args, stdout=out)
        return out.getvalue()

    @override_settings(ATTENDANCE_EXEMPT_ROLES=['chair'])
    def test_days_are_built_from_the_log(self):
        self.assertIn('Hech narsa yozilmadi', self.run_command())
        self.assertFalse(Attendance.objects.exists())
        with patch('core.telegram.message') as message:
            self.run_command('--apply')
        message.assert_not_called()
        first = Attendance.objects.get(date='2026-10-26')
        self.assertEqual((first.entry_count, first.status), (2, 'present'))     # 06:40 is before the day starts
        self.assertEqual(timezone.localtime(first.first_entry_at).strftime('%H:%M'), '09:20')
        self.assertTrue(Attendance.objects.filter(date='2026-10-27').exists())

    @override_settings(ATTENDANCE_EXEMPT_ROLES=['chair'])
    def test_existing_days_are_left_alone_and_a_rerun_adds_nothing(self):
        self.run_command('--apply')
        Attendance.objects.filter(date='2026-10-27').update(entry_count=9)
        self.assertIn('0 kun tiklandi, 2 kun allaqachon bor edi', self.run_command('--apply'))
        self.assertEqual(Attendance.objects.get(date='2026-10-27').entry_count, 9)

    @override_settings(ATTENDANCE_EXEMPT_ROLES=['chair', 'deputy'])
    def test_refuses_while_the_role_is_still_exempt(self):
        with self.assertRaises(CommandError):
            self.run_command('--apply')
