"""Time-based attendance bot alerts; safe to run hourly.

From ATTENDANCE_LATE_ALERT_HOUR (10:00 Tashkent) each enrolled employee who has
not arrived gets a "you're running late" bot message. From ATTENDANCE_ABSENT_HOUR
(11:00) those still missing are told they are marked absent, with a way to give a
reason, and the secretaries get the day's absentee list. Weekends are skipped.
Each alert is sent once per day — the `AttendanceAlert` marker makes re-runs
idempotent, so an hourly schedule is fine.

    python manage.py attendance_alerts
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from core import telegram
from core.attendance import alert_once, secretaries, tracked
from core.models import AccessCard, Attendance, AttendanceAlert, AttendanceExcuse, User


class Command(BaseCommand):
    help = ('Kechikish (10:00), kelmadi (11:00) va kelmaganlar ro‘yxati bo‘yicha bot ogohlantirishlari. '
            'Har soat ishga tushsa bo‘ladi.')

    def handle(self, *args, **options):
        if not telegram.configured():
            self.stdout.write('Telegram sozlanmagan; ogohlantirish yuborilmadi.')
            return
        now = timezone.localtime()
        today = now.date()
        if today.weekday() in settings.ATTENDANCE_WEEKEND_DAYS:
            self.stdout.write('Dam olish kuni; ogohlantirish yuborilmadi.')
            return
        expected = list(tracked(User.objects.filter(is_active=True,
                                pk__in=AccessCard.objects.filter(active=True).values('user'))))
        today_rows = Attendance.objects.filter(date=today)
        # A rejected day is not an arrival; a pending review still is (decision open).
        rejected = set(today_rows.filter(status=Attendance.Status.REJECTED).values_list('user_id', flat=True))
        arrived = set(today_rows.exclude(status=Attendance.Status.REJECTED).values_list('user_id', flat=True))
        # A known reason (sick, on leave, coming later) excuses the absence: no warning
        # for that person, and the secretary sees them apart, with the reason.
        excuses = {e.user_id: e for e in AttendanceExcuse.objects.filter(date=today)}
        absentees = [person for person in expected if person.pk not in arrived and person.pk not in excuses]
        excused = [person for person in expected if person.pk not in arrived and person.pk in excuses]
        late_sent = absent_sent = list_sent = 0

        # Only between the two hours: someone first reached after 11:00 gets the
        # absence message alone, not a late reminder in the same breath.
        if settings.ATTENDANCE_LATE_ALERT_HOUR <= now.hour < settings.ATTENDANCE_ABSENT_HOUR:
            for person in absentees:
                if alert_once(person, today, AttendanceAlert.Kind.LATE):
                    if telegram.message(person, f'⏰ Ishga kechikyapsiz: soat {now:%H:%M}, siz bugun '
                                                f'({today:%d.%m.%Y}) hali kirmadingiz. Ish '
                                                f'{settings.ATTENDANCE_LATE_HOUR}:00 dan boshlanadi. Sababingiz '
                                                f'bo‘lsa, ruxsat so‘rang.', reverse('my_excuse_request')):
                        late_sent += 1

        if now.hour >= settings.ATTENDANCE_ABSENT_HOUR:
            reason_url = f"{reverse('my_excuse_request')}?date={today:%Y-%m-%d}"
            for person in absentees:
                if alert_once(person, today, AttendanceAlert.Kind.ABSENT):
                    if telegram.message(person, f'Bugun ({today:%d.%m.%Y}) soat '
                                                f'{settings.ATTENDANCE_ABSENT_HOUR}:00 holatiga ishga kelmagan deb '
                                                f'belgilandingiz. Sababingiz bo‘lsa, «Sababini bildirish» orqali '
                                                f'yuboring — kotib referent ko‘rib chiqadi.', reason_url):
                        absent_sent += 1
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
                    list_sent += 1

        self.stdout.write(self.style.SUCCESS(
            f'{late_sent} kechikish, {absent_sent} kelmadi xabari, {list_sent} kelmaganlar ro‘yxati yuborildi.'))
