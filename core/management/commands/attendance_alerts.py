"""Time-based attendance bot alerts; safe to run hourly.

From ATTENDANCE_LATE_HOUR (09:00 Tashkent) each enrolled employee who has not
arrived gets a "you're late" bot message. From ATTENDANCE_ABSENT_HOUR (11:00) the
secretaries get the day's absentee list. Each alert is sent once per day — the
`AttendanceAlert` marker makes re-runs idempotent, so an hourly schedule is fine.

    python manage.py attendance_alerts
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core import telegram
from core.attendance import alert_once, secretaries
from core.models import AccessCard, Attendance, AttendanceAlert, AttendanceExcuse, User


class Command(BaseCommand):
    help = 'Kechikish (09:00) va kelmaganlar ro‘yxati (11:00) bo‘yicha bot ogohlantirishlari. Har soat ishga tushsa bo‘ladi.'

    def handle(self, *args, **options):
        if not telegram.configured():
            self.stdout.write('Telegram sozlanmagan; ogohlantirish yuborilmadi.')
            return
        now = timezone.localtime()
        today = now.date()
        expected = list(User.objects.filter(is_active=True,
                        pk__in=AccessCard.objects.filter(active=True).values('user')))
        today_rows = Attendance.objects.filter(date=today)
        # A rejected day is not an arrival; a pending review still is (decision open).
        rejected = set(today_rows.filter(status=Attendance.Status.REJECTED).values_list('user_id', flat=True))
        arrived = set(today_rows.exclude(status=Attendance.Status.REJECTED).values_list('user_id', flat=True))
        # A known reason (sick, on leave, coming later) excuses the absence: no late
        # warning for that person, and the secretary sees them apart, with the reason.
        excuses = {e.user_id: e for e in AttendanceExcuse.objects.filter(date=today)}
        absentees = [person for person in expected if person.pk not in arrived and person.pk not in excuses]
        excused = [person for person in expected if person.pk not in arrived and person.pk in excuses]
        late_sent = absent_sent = 0

        if now.hour >= settings.ATTENDANCE_LATE_HOUR:
            for person in absentees:
                if alert_once(person, today, AttendanceAlert.Kind.LATE):
                    if telegram.message(person, f'Eslatma: siz bugun ({today:%d.%m.%Y}) hali ishga '
                                                f'kelmadingiz. Toshkent vaqti bilan soat '
                                                f'{settings.ATTENDANCE_LATE_HOUR}:00 dan oshdi.'):
                        late_sent += 1

        if now.hour >= settings.ATTENDANCE_ABSENT_HOUR:
            names = '\n'.join(f'• {person.full_name}' + (' (rad etilgan)' if person.pk in rejected else '')
                              for person in absentees) or '— yo‘q.'
            summary = (f'Bugun ({today:%d.%m.%Y}) soat {settings.ATTENDANCE_ABSENT_HOUR}:00 '
                       f'holatiga kelmaganlar ({len(absentees)}):\n{names}')
            if excused:
                summary += f'\n\nUzrli ({len(excused)}):\n' + '\n'.join(
                    f'• {person.full_name} — {excuses[person.pk].label}' for person in excused)
            for secretary in secretaries():
                if alert_once(secretary, today, AttendanceAlert.Kind.ABSENTEES):
                    telegram.message(secretary, summary)
                    absent_sent += 1

        self.stdout.write(self.style.SUCCESS(
            f'{late_sent} kechikish ogohlantirishi, {absent_sent} kelmaganlar ro‘yxati yuborildi.'))
