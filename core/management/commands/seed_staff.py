"""Create the real staff list on a production database.

Unlike seed_demo this adds no tasks and no passwords: accounts cannot sign in
until the chair sets a password on the secure page. Existing accounts are never
touched, so the command can be run again after the roster changes.
"""
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ...models import Department, User

DEFAULT_PASSWORD = '12345678'
RAHBARIYAT = 'Rahbariyat'
ADMINISTRATSIYA = 'Boshqaruv administratsiyasi'
BUXGALTERIYA = 'Buxgalteriya va MHXS bo‘yicha moliyaviy hisobot yuritish bo‘limi'
MOLIYA = 'Moliyaviy rivojlantirish va tahlil qilish bo‘limi'
QURILISH = 'Qurilishni muvofiqlashtirish va renovatsiya bo‘yicha loyiha ofisi'
KOMMUNIKATSIYA = 'Kommunikatsiya tarmoqlari va infratuzilmani rivojlantirish loyiha ofisi'
INVESTOR = 'Loyihalarni moliyalashtirish va investorlar bilan aloqalar bo‘limi'
METODOLOGIYA = 'Metodologiya va korporativ rivojlanish bo‘limi'
AKTIVLAR = 'Aktivlar bilan ishlash bo‘limi'
RAQAMLASHTIRISH = 'Raqamlashtirish va axborot xizmati bo‘limi'
YURIDIK = 'Yuridik bo‘lim'
INSON = 'Inson resurslarini rivojlantirish xizmati'
DEVONXONA = 'Devonxona'
TURIZM = 'Turizm va xizmat ko‘rsatishni rivojlantirish bo‘yicha loyiha ofisi'

# username, F.I.Sh., department, role, lavozim
ROSTER = [
    ('abilov', 'Abilov Feruz Ne’matullayevich', None, 'chair', 'Boshqaruv raisi'),
    ('musinov', 'Musinov Dilshod Sultonovich', RAHBARIYAT, 'head',
     'Iqtisodiyot, moliya va strategik rivojlantirish masalalari bo‘yicha Boshqaruv raisining birinchi o‘rinbosari'),
    ('xaitov', 'Xaitov Orif Boliqulovich', RAHBARIYAT, 'employee',
     'Boshqaruv raisining Loyiha boshqaruvi bo‘yicha o‘rinbosari v.b.'),
    ('raximov', 'Raximov Zafar Ismatovich', ADMINISTRATSIYA, 'head', 'Boshqaruv administratsiyasi rahbari'),
    ('ergashev', 'Ergashev Jahongir Yusufxonovich', ADMINISTRATSIYA, 'employee', 'Boshqaruv administratsiyasi bosh mutaxassisi'),
    ('usmonov', 'Usmonov Umar Nizomiddinovich', BUXGALTERIYA, 'head', 'Bo‘lim boshlig‘i — Bosh hisobchi'),
    ('abdullayev', 'Abdullayev Shahzodxon Zafar o‘g‘li', BUXGALTERIYA, 'employee', 'Bosh hisobchi o‘rinbosari'),
    ('yarmatov', 'Yarmatov Ne’matjon Ixalovich', MOLIYA, 'head', 'Bo‘lim bosh mutaxassisi'),
    ('tuyliyev', 'Tuyliyev Asliddin Keldiyor o‘g‘li', QURILISH, 'head', 'Loyiha ofisi rahbari v.v.b.'),
    ('xalimov', 'Xalimov Farrux Zafarzoda', QURILISH, 'employee', 'Loyiha ofisi bosh mutaxassisi'),
    ('luqmonov', 'Luqmonov Hamidulla Ikromovich', QURILISH, 'employee', 'Loyiha ofisi yetakchi mutaxassisi'),
    ('istamov', 'Istamov Firdavs Yunusovich', KOMMUNIKATSIYA, 'head', 'Loyiha ofisi rahbari'),
    ('shonazarov', 'Shonazarov Shodiyor Shonazar o‘g‘li', KOMMUNIKATSIYA, 'employee', 'Loyiha ofisi bosh mutaxassisi'),
    ('marupov', 'Marupov Doniyor Xasanjonovich', KOMMUNIKATSIYA, 'employee', 'Loyiha ofisi yetakchi mutaxassisi'),
    ('xoshimov', 'Xoshimov Nurmuhammad Amin o‘g‘li', INVESTOR, 'head', 'Bo‘lim bosh mutaxassisi'),
    ('jorayev', 'Jo‘rayev Bahodir Nishonovich', INVESTOR, 'employee', 'Bo‘lim yetakchi mutaxassisi'),
    ('kamolov', 'Kamolov Akmal Abdumalikovich', METODOLOGIYA, 'head', 'Bo‘lim bosh mutaxassisi'),
    ('quvandikov', 'Quvandikov Muxtor Narzullayevich', AKTIVLAR, 'head', 'Bo‘lim boshlig‘i'),
    ('ahmedov', 'Ahmedov Ilyos Izzatullo o‘g‘li', AKTIVLAR, 'employee', 'Bo‘lim bosh mutaxassisi'),
    ('xudayarov', 'Xudayarov Sherzod Erkinovich', RAQAMLASHTIRISH, 'head', 'Bo‘lim boshlig‘i'),
    ('azamov', 'A’zamov Anas Asliddin o‘g‘li', RAQAMLASHTIRISH, 'employee', 'Bo‘lim bosh mutaxassisi'),
    ('bayzakov', 'Bayzakov Dilshod Ilhomovich', YURIDIK, 'head', 'Yuridik bo‘lim boshlig‘i'),
    ('xolmuradov', 'Xolmuradov Zafar Ilhomovich', INSON, 'head', 'Xizmat rahbari'),
    ('isomiddinov', 'Isomiddinov Izzatullo Muxtor o‘g‘li', DEVONXONA, 'office', 'Devonxona mudiri'),
    ('tosheva', 'Tosheva Nargiza Farmonovna', DEVONXONA, 'secretary', 'Kotib referent'),
    ('oblonov', 'Oblonov Shahzod Sirojiddin o‘g‘li', TURIZM, 'head', 'Loyiha ofisi yetakchi mutaxassisi'),
]


class Command(BaseCommand):
    help = 'Tashkilot xodimlari ro‘yxatini yaratadi. Mavjud hisoblarga tegmaydi.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Faqat ko‘rsatadi, hech narsa yozmaydi.')
        parser.add_argument('--password', default=DEFAULT_PASSWORD,
                            help=f'Yaratilgan hisoblar uchun boshlang‘ich umumiy parol. Standart: {DEFAULT_PASSWORD}.')
        parser.add_argument('--no-password', action='store_true',
                            help='Parolsiz yaratadi: hisoblarga rais Struktura sahifasida parol belgilaydi.')

    @transaction.atomic
    def handle(self, *args, **options):
        password = None if options['no_password'] else options['password']
        if password and len(password) < 8:
            raise CommandError('Vaqtinchalik parol kamida 8 belgidan iborat bo‘lsin.')
        if password:
            try:
                validate_password(password)
            except DjangoValidationError:
                # Allowed on purpose for a first rollout, but never left in place.
                self.stdout.write(self.style.WARNING(
                    'DIQQAT: bu parol oson topiladi va barcha hisoblar uchun bir xil bo‘ladi. '
                    'Xodimlar tizimga kirgach, uni albatta almashtirsin.'))
        created, skipped = [], []
        for username, full_name, department_name, role, job_title in ROSTER:
            if User.objects.filter(username=username).exists():
                skipped.append(username)
                continue
            department = None
            if department_name:
                department, _ = Department.objects.get_or_create(name=department_name)
            user = User(username=username, full_name=full_name, role=role,
                        job_title=job_title, department=department)
            if password:
                user.set_password(password)
            else:
                # The chair sets each password on the secure page; nobody signs in until then.
                user.set_unusable_password()
            if not options['dry_run']:
                user.save()
                if role == User.Role.HEAD and department and not department.head_id:
                    department.head = user
                    department.save(update_fields=['head'])
            created.append(f'{username} — {full_name} ({job_title})')
        for line in created:
            self.stdout.write('+ ' + line)
        if skipped:
            self.stdout.write(f'Mavjud hisoblar o‘zgartirilmadi: {", ".join(skipped)}')
        self.stdout.write(self.style.SUCCESS(
            f'{len(created)} ta xodim {"tayyorlandi (dry-run)" if options["dry_run"] else "qo‘shildi"}. '
            + (f'Boshlang‘ich parol: {password}. Xodimlar uni almashtirsin.' if password
               else 'Parollar berilmadi: rais Struktura sahifasida belgilaydi.')))
        if options['dry_run']:
            transaction.set_rollback(True)
