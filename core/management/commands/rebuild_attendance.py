"""Build missing attendance days from the door log.

For people whose entries reached the door log but never became attendance — they
were in ATTENDANCE_EXEMPT_ROLES at the time, or the server was down — this folds
the logged entries into days, the way live ingest would. Days that already have
attendance are left alone. No bot message is sent and no face is checked (the
frames of those moments were never kept), so a rebuilt day counts on the card.

    python manage.py rebuild_attendance --role deputy
    python manage.py rebuild_attendance --user musinov --since 2026-10-21 --apply
"""
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.attendance import is_exempt, settle_day
from core.models import AccessEvent, Attendance, User


class Command(BaseCommand):
    help = 'Eshik jurnalidagi kirishlardan yo‘q kunlik davomatni tiklaydi (xabar yuborilmaydi).'

    def add_arguments(self, parser):
        parser.add_argument('--user', action='append', default=[], help='Login (takrorlasa bo‘ladi).')
        parser.add_argument('--role', action='append', default=[], help='Rol, masalan deputy (takrorlasa bo‘ladi).')
        parser.add_argument('--since', help='Shu sanadan (YYYY-MM-DD). Standart: jurnal boshidan.')
        parser.add_argument('--until', help='Shu sanagacha (YYYY-MM-DD). Standart: bugun.')
        parser.add_argument('--apply', action='store_true', help='Bazaga yozish. Busiz faqat ko‘rsatadi.')

    def handle(self, *args, **options):
        if not options['user'] and not options['role']:
            raise CommandError('--user yoki --role bering.')
        people = User.objects.filter(is_active=True)
        people = people.filter(username__in=options['user']) | people.filter(role__in=options['role'])
        people = list(people.distinct().order_by('full_name'))
        if not people:
            raise CommandError('Bunday faol foydalanuvchi topilmadi.')
        exempt = [person for person in people if is_exempt(person)]
        if exempt:
            raise CommandError('Bu xodimlar hali ATTENDANCE_EXEMPT_ROLES da: '
                               + ', '.join(person.username for person in exempt)
                               + '. Avval .env dan rolni olib tashlang va servisni qayta ishga tushiring.')
        try:
            since = date.fromisoformat(options['since']) if options['since'] else None
            until = date.fromisoformat(options['until']) if options['until'] else timezone.localdate()
        except ValueError:
            raise CommandError('Sana YYYY-MM-DD ko‘rinishida bo‘lsin.')

        events = AccessEvent.objects.filter(user__in=people).order_by('happened_at', 'pk')
        days = {}
        for event in events:
            moment = timezone.localtime(event.happened_at)
            if moment.hour < settings.ATTENDANCE_DAY_START_HOUR:
                continue                     # before the day starts: logged, never counted
            day = moment.date()
            if (since and day < since) or day > until:
                continue
            days.setdefault((event.user_id, day), []).append(event)
        existing = set(Attendance.objects.filter(user__in=people).values_list('user_id', 'date'))
        missing = {key: entries for key, entries in days.items() if key not in existing}

        names = {person.pk: person for person in people}
        with transaction.atomic():
            for (user_id, day), entries in sorted(missing.items(), key=lambda item: (names[item[0][0]].full_name, item[0][1])):
                first, last = entries[0], entries[-1]
                self.stdout.write(f'+ {names[user_id].full_name} — {day:%d.%m.%Y}: {len(entries)} ta kirish, '
                                  f'{timezone.localtime(first.happened_at):%H:%M}–{timezone.localtime(last.happened_at):%H:%M}')
                if options['apply']:
                    attendance = Attendance.objects.create(
                        user_id=user_id, date=day, entry_count=len(entries), first_entry_at=first.happened_at,
                        last_entry_at=last.happened_at, first_event=first)
                    settle_day(attendance)
            skipped = len(days) - len(missing)
            summary = f'{len(missing)} kun {"tiklandi" if options["apply"] else "tiklanadi"}'
            summary += f', {skipped} kun allaqachon bor edi.' if skipped else '.'
            self.stdout.write(self.style.SUCCESS(summary))
            if not options['apply']:
                self.stdout.write(self.style.WARNING('Hech narsa yozilmadi. Tekshirib, --apply bilan qayta ishga tushiring.'))
                transaction.set_rollback(True)
