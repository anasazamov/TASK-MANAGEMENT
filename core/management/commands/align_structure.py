"""Bring departments and deputies' reach in line with the approved organization chart.

The chart ("vaqtinchalik tashkiliy tuzilma") puts five units under the first deputy
(CFO), four under the project deputy (CPO), three under the investment deputy (CIO)
and seven directly under the chair. This command:

- renames existing departments to their name on the chart,
- creates the units that are missing,
- gives each deputy exactly the units of their own line; units that answer to the
  chair directly are supervised by no deputy.

It deletes nothing and moves nobody. Departments that are not on the chart are only
reported. Without --apply it shows what would change and writes nothing.

    python manage.py align_structure
    python manage.py align_structure --cfo login1 --cpo login2 --apply
"""
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Department, Task, User

# line -> [(name on the chart, pattern that recognizes an existing department)]
CHART = {
    'cfo': [
        ('Buxgalteriya va MHXS bo‘yicha moliyaviy hisobot yuritish bo‘limi', r'buxgalter'),
        ('Samarqand viloyatini rivojlantirish jamg‘armasini boshqarish bo‘limi', r'jamg'),
        ('Moliyaviy rivojlantirish va tahlil qilish bo‘limi', r'moliyaviya? rivojlan'),
        ('Strategik rivojlantirish bo‘limi', r'strategik'),
        ('Xaridlar sho‘basi', r'xarid'),
    ],
    'cpo': [
        ('Qurilishni muvofiqlashtirish va renovatsiya bo‘yicha loyiha ofisi', r'qurilish'),
        ('Turizm va xizmat ko‘rsatishni rivojlantirish bo‘yicha loyiha ofisi', r'turizm'),
        ('Kommunikatsiya tarmoqlari va infratuzilmani rivojlantirish loyiha ofisi', r'kommunikatsiya'),
        ('Davlat-xususiy sherikchilik loyihalarini amalga oshirish bo‘yicha loyiha ofisi', r'davlat.xususiy|dxsh'),
    ],
    'cio': [
        ('Loyiha tahlili bo‘limi', r'loyiha tahlil'),
        ('Loyihalarni moliyalashtirish va investorlar bilan aloqalar bo‘limi', r'moliyalashtirish|investor'),
        ('Monitoring bo‘limi', r'monitoring'),
    ],
    'chair': [
        ('Boshqaruv administratsiyasi', r'administratsiya'),
        ('Metodologiya va korporativ rivojlanish bo‘limi', r'metodologiya'),
        ('Yuridik xizmati', r'yuridik'),
        ('Inson resurslarini rivojlantirish xizmati', r'inson resurs'),
        ('Devonxona', r'devonxona'),
        ('Aktivlar bilan ishlash bo‘limi', r'aktiv'),
        ('Raqamlashtirish va axborot xizmati bo‘limi', r'raqamlashtirish'),
    ],
}
LINES = {'cfo': 'Birinchi o‘rinbosar (CFO)', 'cpo': 'Loyiha boshqaruvi bo‘yicha o‘rinbosar (CPO)',
         'cio': 'Investitsiya bo‘yicha o‘rinbosar (CIO)', 'chair': 'Raisga bevosita'}
# How a deputy's job title names their line, when no login is given.
TITLES = {'cfo': r'cfo|iqtisod|moliya', 'cpo': r'cpo|loyiha boshqaruv', 'cio': r'cio|investitsiya'}


def plain(text):
    """Lower case with every apostrophe variant removed, for forgiving comparison."""
    return re.sub(r'\s+', ' ', re.sub(r"[‘’'`ʻʼ]", '', text.lower())).strip()


def match_departments(departments):
    """{chart name: existing department or None}, and the departments the chart does not know."""
    found, used = {}, {}
    for units in CHART.values():
        for name, pattern in units:
            hits = [d for d in departments if re.search(pattern, plain(d.name))]
            if len(hits) > 1:
                raise CommandError(f'«{name}» uchun bir nechta bo‘linma mos keldi: '
                                   + ', '.join(f'«{d.name}»' for d in hits) + '. Avval ortiqchasini birlashtiring.')
            if hits and hits[0].pk in used:
                raise CommandError(f'«{hits[0].name}» ikki birlikka mos keldi: «{used[hits[0].pk]}» va «{name}».')
            if hits:
                used[hits[0].pk] = name
            found[name] = hits[0] if hits else None
    return found, [d for d in departments if d.pk not in used]


def find_deputies(logins):
    """{line: deputy or None}: by the login given, else by what the job title says."""
    deputies = list(User.objects.filter(is_active=True, role=User.Role.DEPUTY).order_by('full_name'))
    chosen = {}
    for line in ('cfo', 'cpo', 'cio'):
        if logins.get(line):
            person = next((d for d in deputies if d.username == logins[line]), None)
            if not person:
                raise CommandError(f'--{line} {logins[line]}: bunday faol o‘rinbosar yo‘q.')
            chosen[line] = person
    for line in ('cfo', 'cpo', 'cio'):
        if line in chosen:
            continue
        hits = [d for d in deputies if d not in chosen.values() and re.search(TITLES[line], plain(d.job_title))]
        if len(hits) > 1:
            raise CommandError(f'{LINES[line]}: lavozimi bo‘yicha bir nechta o‘rinbosar mos keldi ('
                               + ', '.join(d.username for d in hits) + f'). --{line} LOGIN bilan aniq ko‘rsating.')
        chosen[line] = hits[0] if hits else None
    left = [d for d in deputies if d not in chosen.values()]
    if left:
        raise CommandError('Qaysi yo‘nalishga tegishli ekani aniqlanmagan o‘rinbosar: '
                           + ', '.join(f'{d.username} («{d.job_title}»)' for d in left)
                           + '. --cfo, --cpo yoki --cio LOGIN bilan ko‘rsating.')
    return chosen


class Command(BaseCommand):
    help = 'Bo‘linmalar va o‘rinbosarlarning nazorat doirasini tashkiliy tuzilmaga moslaydi.'

    def add_arguments(self, parser):
        for line in ('cfo', 'cpo', 'cio'):
            parser.add_argument(f'--{line}', help=f'{LINES[line]} logini (lavozimidan aniqlanmasa).')
        parser.add_argument('--apply', action='store_true', help='Bazaga yozish. Busiz faqat ko‘rsatadi.')

    @transaction.atomic
    def handle(self, *args, **options):
        say = self.stdout.write
        found, unknown = match_departments(list(Department.objects.order_by('name')))
        deputies = find_deputies({line: options[line] for line in ('cfo', 'cpo', 'cio')})
        before = {d.pk: Task.objects.visible_to(d).count() for d in deputies.values() if d}

        say('BO‘LINMALAR')
        resolved = {}
        for line, units in CHART.items():
            say(f'  {LINES[line]}:')
            for name, _ in units:
                department = found[name]
                if not department:
                    department = Department.objects.create(name=name)
                    say(f'    + yaratiladi: {name}')
                elif department.name != name:
                    say(f'    ~ nomi o‘zgaradi: «{department.name}» → «{name}»')
                    department.name = name
                    department.save(update_fields=['name'])
                else:
                    say(f'    = {name}')
                resolved[name] = department
        if unknown:
            say('  Tuzilmada yo‘q (tegilmaydi, o‘zingiz hal qiling):')
            for department in unknown:
                head = f', boshlig‘i: {department.head.full_name}' if department.head else ''
                say(f'    ? «{department.name}» — {department.employees.filter(is_active=True).count()} xodim{head}')

        say('O‘RINBOSARLAR')
        for line in ('cfo', 'cpo', 'cio'):
            person, wanted = deputies[line], [resolved[name] for name, _ in CHART[line]]
            if not person:
                say(f'  {LINES[line]}: tizimda yo‘q. Uning {len(wanted)} ta birligi hozircha raisga bevosita bo‘ysunadi.')
                continue
            current = set(person.supervised.values_list('pk', flat=True))
            target = {d.pk for d in wanted}
            say(f'  {LINES[line]}: {person.full_name} ({person.username}) — nazoratida {len(current)} ta, bo‘ladi {len(target)} ta')
            for department in Department.objects.filter(pk__in=current - target).order_by('name'):
                say(f'    - olinadi: {department.name}')
            for department in Department.objects.filter(pk__in=target - current).order_by('name'):
                say(f'    + qo‘shiladi: {department.name}')
            person.supervised.set(wanted)
            after = Task.objects.visible_to(User.objects.get(pk=person.pk)).count()
            if after != before[person.pk]:
                say(f'    ! ko‘radigan topshiriqlari: {before[person.pk]} → {after}')
            led = Department.objects.filter(head=person).first()
            if led:
                say(f'    ! «{led.name}» bo‘linmasining boshlig‘i ham qilib belgilangan — tuzilmada bunday emas.')

        if options['apply']:
            say(self.style.SUCCESS('Saqlandi.'))
        else:
            transaction.set_rollback(True)
            say(self.style.WARNING('Hech narsa yozilmadi. Saqlash uchun --apply qo‘shing.'))
