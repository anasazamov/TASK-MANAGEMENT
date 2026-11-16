"""Fill employees' "Malakasi va tajribasi" from a prepared JSON file.

The file is a list of {"name": "Familiya Ism Otasining ismi", "expertise": "..."}.
It is prepared outside the repository from the staff biographies and holds
professional facts only (education, speciality, work history). Each name must match
exactly one active employee; anything else is reported and skipped. Without --apply
nothing is written.

    python manage.py import_expertise /root/expertise.json
    python manage.py import_expertise /root/expertise.json --apply
"""
import json

from django.core.management.base import BaseCommand, CommandError

from core.assistant.person_search import find_people
from core.models import User


class Command(BaseCommand):
    help = 'Xodimlarning malaka va tajriba matnini JSON fayldan yuklaydi.'

    def add_arguments(self, parser):
        parser.add_argument('path', help='JSON fayl: [{"name": ..., "expertise": ...}]')
        parser.add_argument('--apply', action='store_true', help='Bazaga yozish. Busiz faqat ko‘rsatadi.')

    def handle(self, *args, **options):
        try:
            with open(options['path'], encoding='utf-8-sig') as source:
                items = json.load(source)
        except (OSError, ValueError) as error:
            raise CommandError(f'Faylni o‘qib bo‘lmadi: {error}')
        if not isinstance(items, list) or not all(isinstance(i, dict) and i.get('name') and i.get('expertise') for i in items):
            raise CommandError('Fayl [{"name": ..., "expertise": ...}] ko‘rinishida bo‘lishi kerak.')
        staff = User.objects.filter(is_active=True, is_superuser=False).select_related('department')
        limit = User._meta.get_field('expertise').max_length
        saved = 0
        for item in items:
            # Surname and first name identify the person; patronymics are spelled too many ways.
            name = ' '.join(str(item['name']).split()[:2])
            found, matching = find_people(staff, name)
            # Only a name counts: a near spelling or a job-title hit is not an identification.
            if len(found) != 1 or matching['match'] not in ('exact', 'inflected', 'spelling'):
                self.stdout.write(self.style.WARNING(
                    f"  ? {item['name']}: " + ('topilmadi' if not found else 'bir nechta yoki noaniq mos keldi: '
                                               + ', '.join(p.full_name for p in found[:4]))))
                continue
            person, text = found[0], str(item['expertise']).strip()[:limit]
            state = 'o‘zgarmaydi' if person.expertise == text else ('yangilanadi' if person.expertise else 'kiritiladi')
            self.stdout.write(f'  {person.full_name} ({person.username}): {state}, {len(text)} belgi')
            if options['apply'] and person.expertise != text:
                person.expertise = text
                person.save(update_fields=['expertise'])
                saved += 1
        self.stdout.write(self.style.SUCCESS(f'Saqlandi: {saved} ta.') if options['apply']
                          else self.style.WARNING('Hech narsa yozilmadi. Saqlash uchun --apply qo‘shing.'))
