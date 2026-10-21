"""Enroll staff faces from the HR staff list (Word), adding missing staff on request.

Each row's name (Cyrillic) is transliterated and matched to an existing account
the same way door-station names are; the row's photo becomes that person's
reference face. Nothing is written without --apply. An existing face is kept
unless --replace is given. With --create, rows with no account get one, like
seed_staff does: no password, so nobody signs in until the chair sets it.

    python manage.py import_staff_faces "Xodimlar ro'yxati.docx"
    python manage.py import_staff_faces "Xodimlar ro'yxati.docx" --create --apply
"""
import re
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core import access_cards, staff_doc
from core.attendance import enroll_face
from core.models import FaceEnrollment, User

STATES = {'sure': 'aniq', 'likely': 'taxminiy', 'manual': 'qo‘lda', 'unmatched': 'topilmadi',
          'skipped': 'o‘tkazildi'}


def username_for(name):
    surname = re.sub(r'[^a-z]', '', staff_doc.latin(name.split()[0]).lower().replace('‘', '').replace('’', ''))
    candidate, number = surname or 'xodim', 1
    while User.objects.filter(username__iexact=candidate).exists():
        number += 1
        candidate = f'{surname}{number}'
    return candidate


class Command(BaseCommand):
    help = 'Xodimlar ro‘yxatidagi (Word) rasmlarni xodimlarning yuzi sifatida biriktiradi.'

    def add_arguments(self, parser):
        parser.add_argument('path', help='Xodimlar ro‘yxati: .docx (yoki LibreOffice bo‘lsa .doc).')
        parser.add_argument('--map', action='append', default=[], metavar='T/R=USER',
                            help='Qo‘lda bog‘lash: jadvaldagi tartib raqami = login yoki ID.')
        parser.add_argument('--skip', action='append', default=[], metavar='T/R', help='Bu qatorni o‘tkazish.')
        parser.add_argument('--likely', action='store_true', help='Taxminiy mosliklarni ham yozish.')
        parser.add_argument('--create', action='store_true', help='Hisobi yo‘q xodimlarni yaratish (parolsiz).')
        parser.add_argument('--replace', action='store_true', help='Mavjud yuzni yangisi bilan almashtirish.')
        parser.add_argument('--apply', action='store_true', help='Bazaga yozish. Busiz faqat ko‘rsatadi.')

    def manual(self, pairs):
        result = {}
        for pair in pairs:
            number, _, ref = pair.partition('=')
            ref = ref.strip()
            active = User.objects.filter(is_active=True)
            user = active.filter(username__iexact=ref).first() or (
                active.filter(pk=int(ref)).first() if ref.isdigit() else None)
            if not number.strip() or not user:
                raise CommandError(f'--map noto‘g‘ri yoki foydalanuvchi topilmadi: {pair}')
            result[number.strip()] = user
        return result

    def handle(self, *args, **options):
        try:
            people = staff_doc.read(options['path'])
        except (OSError, ValueError, KeyError) as error:
            raise CommandError(f'Faylni o‘qib bo‘lmadi: {error}')
        except Exception as error:     # a broken archive or a failed LibreOffice run
            raise CommandError(f'Faylni o‘qib bo‘lmadi ({type(error).__name__}).')
        if not people:
            raise CommandError('Jadvalda rasmli xodim topilmadi.')
        users = list(User.objects.filter(is_active=True).exclude(full_name=''))
        rows = access_cards.match([{'employee_no': str(i), 'name': p['name'], 'cards': []}
                                   for i, p in enumerate(people, 1)],
                                  users, self.manual(options['map']), set(options['skip']))
        enrolled = set(FaceEnrollment.objects.values_list('user_id', flat=True))
        done = {'face': 0, 'kept': 0, 'created': 0, 'failed': 0}
        with transaction.atomic():
            for row, person in zip(rows, people):
                take = row.state in ('sure', 'manual') or (row.state == 'likely' and options['likely'])
                note = ''
                if row.state == 'unmatched' and options['create']:
                    note = 'yangi hisob yaratiladi'
                    if options['apply']:
                        row.user = User(username=username_for(person['name']), full_name=person['name'],
                                        role=User.Role.EMPLOYEE, job_title=person['position'][:180],
                                        phone=person['phone'])
                        row.user.set_unusable_password()
                        row.user.save()
                        done['created'] += 1
                        note = f'yangi hisob: {row.user.username}'
                    take = True
                if take and row.user and row.user.pk in enrolled and not options['replace']:
                    note, take = 'yuzi bor, o‘zgartirilmadi (--replace)', False
                    done['kept'] += 1
                if take and options['apply'] and row.user:
                    try:
                        enroll_face(row.user, BytesIO(person['photo']))
                        done['face'] += 1
                        note = (note + '; ' if note else '') + 'yuz biriktirildi'
                    except ValidationError as error:
                        done['failed'] += 1
                        note = (note + '; ' if note else '') + ' '.join(error.messages)
                target = f'{row.user.full_name} ({row.user.username})' if row.user and row.user.pk else '—'
                if row.state == 'unmatched' and row.runner_up and not row.user:
                    target = f'— (eng yaqini: {row.runner_up.full_name}, {row.score:.2f})'
                line = f'{STATES[row.state]:10} {row.employee_no:>2}. {person["name"][:38]:38} -> {target}'
                if row.state in ('sure', 'likely'):
                    line += f' [{row.score:.2f}]'
                if note:
                    line += f'  {note}'
                style = self.style.WARNING if row.state in ('likely', 'unmatched') else None
                self.stdout.write(style(line) if style else line)
            if not options['apply']:
                transaction.set_rollback(True)
        self.stdout.write('')
        if options['apply']:
            self.stdout.write(self.style.SUCCESS(
                f'Yuz biriktirildi: {done["face"]}, yangi hisob: {done["created"]}, '
                f'mavjud yuz saqlandi: {done["kept"]}, yuz topilmadi: {done["failed"]}.'))
        else:
            self.stdout.write(self.style.WARNING('Hech narsa yozilmadi. Tekshirib, --apply bilan qayta ishga tushiring.'))
