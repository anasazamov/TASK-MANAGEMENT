"""Bind the staff to their door-station cards and ids in one go, like seed_staff.

The pairs below were taken from the DS-KV6124-WBE1 on 21.10.2026 and checked by
name against the production staff list. The station's own test record ("123")
and NODIRA AXMEDOVA, who has no account yet, are left out. A card already bound
to someone else is reported, not moved; re-running changes nothing.
For later roster changes use sync_access_cards, which reads the device itself.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from ... import access_cards
from ...models import User

# username, domofondagi ID (employeeNo), karta raqami, domofondagi ism
CARDS = [
    ('usmonov', '1', '4096409635', 'UMAR USMONOV'),
    ('xolmuradov', '2', '4095777875', 'ZAFAR XOLMURODOV'),
    ('isomiddinov', '3', '4096658019', 'IZZATULLO ISOMIDDINOV'),
    ('abilov', '4', '4096757283', 'FERUZ ABILOV'),
    ('musinov', '5', '4095891443', 'DILSHOD MUSINOV'),
    ('xaitov', '6', '4097517043', 'ORIF XAITOV'),
    ('raximov', '7', '4096823763', 'ZAFAR RAXIMOV'),
    ('ergashev', '8', '4095730707', 'JAXONGIR ERGASHEV'),
    ('istamov', '9', '4097590035', 'FIRDAVS ISTAMOV'),
    ('shonazarov', '10', '4096811907', 'SHODIYOR SHONAZAROV'),
    ('bayzakov', '11', '4097090259', 'DILSHOD BOYZOKOV'),
    ('xudayarov', '12', '4096428195', 'SHERZOD XUDAYOROV'),
    ('azamov', '13', '4096511475', 'ANAS AZAMOV'),
    ('xoshimov', '14', '4096828355', 'NURMUHAMMAD XOSHIMOV'),
    ('jorayev', '15', '4096330147', 'BAHODIR JURAYEV'),
    ('yarmatov', '16', '4096574291', 'NEMATJON YARMATOV'),
    ('narziyev', '17', '4096160019', 'OBIDJON NARZIYEV'),
    ('tosheva', '19', '4097151299', 'NARGIZA TOSHEYEVA'),
    ('marupov', 'b12e5b394fd18b373278b328ee6af0eb', '4097374707', 'DONIYOR MARUPOV'),
    ('oripov', '07578d7f244b70ed3417452996293eea', '4097645011', 'AZIZ ORIPOV'),
    ('tuyliyev', 'fda125ace65ed8ddda40f55f06cbfc70', '4095830627', 'ASLIDDIN TUYLIEV'),
    ('luqmonov', 'fc5e34b925de86a2fba74a363f154887', '4096491843', 'HAMIDULLO LUQMONOV'),
    ('kamolov', 'a937d70a68b4b6dae3a1b003ee20252d', '4097403651', 'AKMAL KAMOLOV'),
    ('abdullayev', 'fe9b8fe6a81e86e8e732c0cdef096d85', '4095772323', 'SHAHZODXON ABDULLAYEV'),
    ('ahmedov', '94d822104954e35ae9bdd6b457250fca', '4097688563', 'ILYOS AXMEDOV'),
    ('xalimov', '1be5bda83747aecf6183e04512fbe1b8', '4096824611', 'FARRUX XOLIMOV'),
    ('quvandikov', '22b873da20add305fd3045798186ef10', '4096746371', 'MUXTOR QUVONDOKOV'),
]

OUTCOMES = {'created': '+', 'updated': '~', 'unchanged': '=', 'conflict': '!'}


class Command(BaseCommand):
    help = 'Xodimlarni domofon kartalari va ID’lariga birdan bog‘laydi. Qayta ishga tushirsa bo‘ladi.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Faqat ko‘rsatadi, hech narsa yozmaydi.')

    @transaction.atomic
    def handle(self, *args, **options):
        users = {user.username.lower(): user for user in User.objects.filter(is_active=True)}
        rows, missing = [], []
        for username, employee_no, card_no, name in CARDS:
            user = users.get(username)
            if not user:
                missing.append(f'{username} ({name})')
                continue
            rows.append(access_cards.Row(employee_no, name, [card_no], user=user, state='sure'))
        access_cards.apply(rows)
        for row in rows:
            self.stdout.write(f'{OUTCOMES[row.outcome]} {row.user.full_name} — ID {row.employee_no}, '
                              f'karta {row.cards[0]}')
            for note in row.notes:
                self.stdout.write(self.style.ERROR(f'    {note}'))
        if missing:
            self.stdout.write(self.style.WARNING('Faol hisobi topilmadi, o‘tkazildi: ' + ', '.join(missing)))
        counts = {key: sum(1 for r in rows if r.outcome == key) for key in OUTCOMES}
        self.stdout.write(self.style.SUCCESS(
            f'Yangi: {counts["created"]}, yangilandi: {counts["updated"]}, o‘zgarmadi: {counts["unchanged"]}, '
            f'ziddiyat: {counts["conflict"]}' + (' (dry-run, hech narsa yozilmadi)' if options['dry_run'] else '')))
        if options['dry_run']:
            transaction.set_rollback(True)
