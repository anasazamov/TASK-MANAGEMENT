import secrets
from datetime import timedelta

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import DeadlineRequest, Department, Event, Notification, Task, User


class Command(BaseCommand):
    help = 'Seed an empty development database with the reviewed design’s example organization.'

    def add_arguments(self, parser):
        parser.add_argument('--password', help='Optional development password for the three demo accounts.')

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Demo data is only allowed with DJANGO_DEBUG=1.')
        if User.objects.exists():
            self.stdout.write('Database already has users; no records or passwords were changed.')
            return
        password = options['password'] or 'Demo-' + secrets.token_urlsafe(10)
        chair = User.objects.create_user(username='rais', password=password, full_name='Abilov Feruz Ne’matullayevich',
                                         job_title='Boshqaruv raisi', role='chair')
        # name, username, full name, role, position
        roster = [
            ('Rahbariyat', 'musinov', 'Musinov Davron Sobirovich', 'employee', 'Boshqaruv raisining birinchi o‘rinbosari'),
            ('Rahbariyat', 'xaitov', 'Xaitov Olim Bahodirovich', 'employee', 'Raisning loyiha boshqaruvi bo‘yicha o‘rinbosari'),
            ('Boshqaruv administratsiyasi', 'raximov', 'Raximov Zafar Ismatovich', 'head', 'Administratsiya rahbari'),
            ('Boshqaruv administratsiyasi', 'ergashev', 'Ergashev Jamshid Yunusovich', 'employee', 'Administratsiya bosh mutaxassisi'),
            ('Buxgalteriya va MHXS', 'usmonov', 'Usmonov Ulug‘bek Nuriddinovich', 'head', 'Bo‘lim boshlig‘i — Bosh hisobchi'),
            ('Buxgalteriya va MHXS', 'abdullayev', 'Abdullayev Sherzod Zokirovich', 'employee', 'Bosh hisobchi o‘rinbosari'),
            ('Moliyaviy tahlil', 'yarmatov', 'Yarmatov Nodir Ilhomovich', 'head', 'Moliyaviy tahlil bo‘limi bosh mutaxassisi'),
            ('Qurilish va renovatsiya loyiha ofisi', 'boshliq', 'Tuyliyev Asliddin Keldiyor', 'head', 'Qurilish va renovatsiya loyiha ofisi rahbari'),
            ('Qurilish va renovatsiya loyiha ofisi', 'xodim', 'Xalimov Farrux Zafarzoda', 'employee', 'Loyiha ofisi bosh mutaxassisi'),
            ('Qurilish va renovatsiya loyiha ofisi', 'luqmonov', 'Luqmonov Xurshid Islomovich', 'employee', 'Loyiha ofisi yetakchi mutaxassisi'),
            ('Kommunikatsiya loyiha ofisi', 'istamov', 'Istamov Firdavs Yunusovich', 'head', 'Kommunikatsiya loyiha ofisi rahbari'),
            ('Kommunikatsiya loyiha ofisi', 'shonazarov', 'Shonazarov Sherzod Shavkatovich', 'employee', 'Loyiha ofisi bosh mutaxassisi'),
            ('Kommunikatsiya loyiha ofisi', 'marupov', 'Marupov Davron Xolmatovich', 'employee', 'Loyiha ofisi yetakchi mutaxassisi'),
            ('Investorlar bilan aloqalar', 'xoshimov', 'Xoshimov Nodir Akmalovich', 'head', 'Bo‘lim bosh mutaxassisi'),
            ('Investorlar bilan aloqalar', 'jorayev', 'Jo‘rayev Bahodir Nishonovich', 'employee', 'Bo‘lim yetakchi mutaxassisi'),
            ('Metodologiya', 'kamolov', 'Kamolov Akmal Anvarovich', 'head', 'Metodologiya bo‘limi bosh mutaxassisi'),
            ('Aktivlar bilan ishlash', 'quvandikov', 'Quvandikov Mirjalol Nuriddinovich', 'head', 'Aktivlar bilan ishlash bo‘limi boshlig‘i'),
            ('Aktivlar bilan ishlash', 'ahmedov', 'Ahmedov Islom Ikromovich', 'employee', 'Aktivlar bo‘limi bosh mutaxassisi'),
            ('Raqamlashtirish va IT', 'xudayarov', 'Xudayarov Sherzod Erkinovich', 'head', 'Raqamlashtirish va IT bo‘limi boshlig‘i'),
            ('Raqamlashtirish va IT', 'azamov', 'A’zamov Aziz Akmalovich', 'employee', 'IT bo‘limi bosh mutaxassisi'),
            ('Yuridik bo‘lim', 'bayzakov', 'Bayzakov Doston Ilhomovich', 'head', 'Yuridik bo‘lim boshlig‘i'),
            ('Inson resurslari', 'xolmuradov', 'Xolmuradov Zafar Ismatovich', 'head', 'Inson resurslari xizmati rahbari'),
            ('Devonxona', 'isomiddinov', 'Isomiddinov Islom Murodovich', 'head', 'Devonxona mudiri'),
            ('Turizm loyiha ofisi', 'oblonov', 'Oblonov Shavkat Sobirovich', 'head', 'Turizm loyiha ofisi yetakchi mutaxassisi'),
        ]
        users = {'rais': chair}
        for name, username, full_name, role, title in roster:
            department, _ = Department.objects.get_or_create(name=name)
            user = User.objects.create_user(username=username, password=password if username in ['boshliq', 'xodim'] else None,
                                            full_name=full_name, role=role, job_title=title, department=department)
            if role == 'head':
                department.head = user
                department.save()
            users[username] = user
        now = timezone.now()
        def add(title, assignee, days, age=10, issuer='rais', parent=None, status='active', description='', hours=None):
            due = now + timedelta(hours=hours) if hours is not None else now + timedelta(days=days) if days is not None else None
            task = Task.objects.create(title=title, description=description or 'Bajarilgan ishlar va natijalarni belgilangan muddatda taqdim eting.',
                assignee=users[assignee], issuer=users[issuer], parent=parent, due_at=due, status=status,
                created_at=now-timedelta(days=age), accepted_at=now-timedelta(days=1) if status=='accepted' else None)
            Event.objects.create(task=task, actor=task.issuer, kind='created', body=f'{task.issuer.short_name} → {task.assignee.short_name}', created_at=task.created_at)
            if parent:
                Event.objects.create(task=parent, actor=task.issuer, kind='delegated', body=f'{task.code}: {title} → {task.assignee.short_name}', created_at=task.created_at)
            if status in ['accepted', 'submitted']:
                Event.objects.create(task=task, actor=task.assignee, kind='submitted', body='Tayyorlangan materiallar tekshirish uchun taqdim etildi.', created_at=now-timedelta(days=1))
                if status == 'accepted':
                    Event.objects.create(task=task, actor=task.issuer, kind='accepted', body='Ijro tekshirildi va qabul qilindi.', created_at=now-timedelta(hours=12))
            return task
        financial = add('2026-yil 9 oylik moliyaviy natijalar bo‘yicha yakuniy hisobot', 'musinov', 1, age=16)
        erp = add('Korporativ ERP va elektron hujjat aylanishini joriy etish', 'xudayarov', -3, age=28)
        add('Balansdagi ijara shartnomalarini huquqiy ekspertizadan o‘tkazish', 'bayzakov', 8)
        registon = add('Registon yo‘nalishi kommunikatsiya tarmoqlari loyiha smetasi', 'istamov', 0, age=20, hours=8,
                       description='Loyiha-smeta hujjatlarini ekspertizaga tayyorlash, qiymat bo‘yicha kelishuv olish.')
        renovation = add('Renovatsiya obyektlari bo‘yicha haftalik monitoring', 'boshliq', 5, age=23)
        inventory = add('Jamiyat balansidagi aktivlar inventarizatsiyasi', 'quvandikov', 15)
        add('Kadrlar zaxirasi ro‘yxatini shakllantirish', 'xolmuradov', -8, status='accepted')
        add('Turizm klasteri investitsiya taqdimotini tayyorlash', 'oblonov', 4)
        add('Korporativ boshqaruv kodeksi loyihasini ishlab chiqish', 'kamolov', 13)
        meeting = add('Kuzatuv kengashi yig‘ilishi materiallarini yig‘ish', 'raximov', -1, status='submitted')
        add('Investorlar bazasini muntazam yangilab borish', 'jorayev', None, age=15,
            description='Yangi murojaatlar va aloqalarni bazaga kiritish — doimiy vazifa, qat’iy muddat belgilanmaydi.')
        add('Kuzatuv kengashi yig‘ilishi uchun zal va hujjatlarni tayyorlash', 'isomiddinov', 0, hours=6)
        add('Renovatsiya obyektlari bo‘yicha 4-chorak ish rejasini tayyorlash', 'boshliq', 12)
        add('MHXS bo‘yicha 9 oylik hisobotni yopish', 'usmonov', 0, parent=financial, hours=20)
        add('Investitsiya portfeli samaradorligi tahlili', 'yarmatov', 0, parent=financial, hours=18)
        add('Loyiha pasportlarini yangilash', 'xodim', -7, age=21, issuer='boshliq', parent=renovation,
            description='Renovatsiya obyektlari pasportiga joriy ko‘rsatkichlarni kiritish.')
        add('Podryadchilar bilan kelishuv yig‘ilishi', 'luqmonov', -9, issuer='boshliq', parent=renovation, status='accepted')
        backup = add('Serverlarni zaxiralash reglamenti', 'azamov', -4, age=20, issuer='xudayarov', parent=erp)
        backup.report_requested_at = now-timedelta(days=1)
        backup.save()
        add('Optik tolali tarmoq ijro chizmalarini kelishish', 'shonazarov', 0, issuer='istamov', parent=registon, hours=2)
        add('Yer uchastkalari kadastr hujjatlari', 'ahmedov', 7, issuer='quvandikov', parent=inventory)
        add('Kengash bayonnomasi loyihasini tayyorlash', 'ergashev', -5, issuer='raximov', parent=meeting, status='accepted')
        DeadlineRequest.objects.create(task=registon, requester=users['istamov'], proposed_due_at=now+timedelta(days=5), reason='Davlat ekspertizasi xulosasi kechikdi')
        Event.objects.create(task=registon, actor=users['istamov'], kind='deadline', body='Muddatni 5 kunga uzaytirish so‘raldi. Sabab: Davlat ekspertizasi xulosasi kechikdi.')
        Notification.objects.create(user=chair, task=registon, title='Muddatni uzaytirish so‘rovi — Istamov F.Y.')
        Notification.objects.create(user=chair, task=meeting, title='Ijro topshirildi — Raximov Z.I.')
        call_command('send_reminders', stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS('Created 25 users, 14 departments and 21 tasks.'))
        self.stdout.write(f'Development logins: rais / boshliq / xodim\nPassword for all three: {password}')
