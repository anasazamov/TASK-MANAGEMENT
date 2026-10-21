"""Bind the door station's persons (cards and PIN ids) to existing users.

Nothing is written without --apply: the first run only shows the proposed
pairs, so a wrong guess is caught before it records anyone's attendance.

    # where the device is reachable (HIKVISION_* in .env):
    python manage.py sync_access_cards
    # on the cloud server, from the agent's export (attendance_agent.py --export-users):
    python manage.py sync_access_cards --file people.json
    python manage.py sync_access_cards --file people.json --map 18=narziyev --skip 038c... --apply
"""
import json

from django.core.management.base import BaseCommand, CommandError

from core import access_cards, hikvision
from core.models import AccessCard, User

STATES = {'sure': 'aniq', 'likely': 'taxminiy', 'manual': 'qo‘lda', 'unmatched': 'topilmadi',
          'skipped': 'o‘tkazildi'}
OUTCOMES = {'created': 'yozildi', 'updated': 'yangilandi', 'unchanged': 'o‘zgarmadi', 'conflict': 'ZIDDIYAT'}


class Command(BaseCommand):
    help = 'Domofondagi xodimlarni (karta va PIN) tizimdagi foydalanuvchilarga bog‘laydi.'

    def add_arguments(self, parser):
        parser.add_argument('--file', help='Agent eksporti (attendance_agent.py --export-users). '
                                           'Berilmasa, domofonning o‘zidan o‘qiladi (HIKVISION_*).')
        parser.add_argument('--map', action='append', default=[], metavar='EMPLOYEE_NO=USER',
                            help='Qo‘lda bog‘lash: domofondagi ID = tizimdagi login yoki ID. '
                                 'Ism bo‘yicha topilmaganlar uchun. Takrorlasa bo‘ladi.')
        parser.add_argument('--skip', action='append', default=[], metavar='EMPLOYEE_NO',
                            help='Bu qurilma xodimini bog‘lamaslik (masalan, sinov yozuvi).')
        parser.add_argument('--likely', action='store_true', help='Taxminiy mosliklarni ham yozish.')
        parser.add_argument('--apply', action='store_true', help='Bazaga yozish. Busiz faqat ko‘rsatadi.')

    def load(self, path):
        if not path:
            try:
                return hikvision.people()
            except hikvision.HikvisionNotConfigured as error:
                raise CommandError(f'{error} Yoki --file bilan agent eksportini bering.')
            except Exception as error:
                raise CommandError(f'Domofondan o‘qib bo‘lmadi ({type(error).__name__}). '
                                   'Server domofonni ko‘rmasa, agentda --export-users qiling va --file bering.')
        try:
            with open(path, encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, ValueError) as error:
            raise CommandError(f'Faylni o‘qib bo‘lmadi: {error}')
        people = data.get('people') if isinstance(data, dict) else None
        if not isinstance(people, list):
            raise CommandError('Fayl formati noto‘g‘ri: "people" ro‘yxati yo‘q.')
        return people

    def manual(self, pairs):
        result = {}
        for pair in pairs:
            employee_no, _, username = pair.partition('=')
            if not employee_no or not username:
                raise CommandError(f'--map qiymati EMPLOYEE_NO=USER bo‘lishi kerak: {pair}')
            username = username.strip()
            active = User.objects.filter(is_active=True)
            # Our user is named by username or, as a number, by its id in the system.
            user = active.filter(username__iexact=username).first() or (
                active.filter(pk=int(username)).first() if username.isdigit() else None)
            if not user:
                raise CommandError(f'Faol foydalanuvchi topilmadi: {username}')
            result[employee_no.strip()] = user
        return result

    def handle(self, *args, **options):
        people = self.load(options['file'])
        users = list(User.objects.filter(is_active=True).exclude(full_name=''))
        rows = access_cards.match(people, users, self.manual(options['map']), set(options['skip']))
        if options['apply']:
            access_cards.apply(rows, include_likely=options['likely'])

        for row in sorted(rows, key=lambda r: (list(STATES).index(r.state), r.name)):
            target = f'{row.user.full_name} ({row.user.username}, id {row.user.pk})' if row.user else '—'
            if row.state == 'unmatched' and row.runner_up:
                target = f'— (eng yaqini: {row.runner_up.full_name}, {row.score:.2f})'
            # The full id: it is what --map and --skip take.
            line = (f'{STATES[row.state]:10} {row.employee_no:4} {row.name[:26]:26} '
                    f'{",".join(row.cards)[:22]:22} -> {target}')
            if row.state in ('sure', 'likely'):
                line += f'  [{row.score:.2f}]'
            if row.outcome:
                line += f'  {OUTCOMES[row.outcome]}'
            style = {'conflict': self.style.ERROR}.get(row.outcome) or {
                'likely': self.style.WARNING, 'unmatched': self.style.WARNING}.get(row.state)
            self.stdout.write(style(line) if style else line)
            for note in row.notes:
                self.stdout.write(self.style.ERROR(f'           {note}'))

        counts = {state: sum(1 for r in rows if r.state == state) for state in STATES}
        self.stdout.write('')
        self.stdout.write(', '.join(f'{STATES[s]}: {n}' for s, n in counts.items() if n))
        bound = set(AccessCard.objects.filter(active=True).values_list('user_id', flat=True))
        matched = {r.user.pk for r in rows if r.user}
        without = [u for u in users if u.pk not in bound and u.pk not in matched]
        if without:
            self.stdout.write('Kartasi yo‘q faol foydalanuvchilar: ' + ', '.join(u.full_name for u in without))
        if not options['apply']:
            self.stdout.write(self.style.WARNING(
                'Hech narsa yozilmadi. Tekshirib, --apply bilan qayta ishga tushiring'
                + (' (taxminiylarni ham yozish uchun --likely).' if counts['likely'] else '.')))
        elif any(r.outcome == 'conflict' for r in rows):
            self.stdout.write(self.style.ERROR('Ziddiyatli qatorlar yozilmadi: --map bilan aniq ko‘rsating.'))
        else:
            self.stdout.write(self.style.SUCCESS('Bog‘lash yozildi.'))
