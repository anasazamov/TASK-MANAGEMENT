"""Listen to the door station and, in phase one, learn what it actually sends.

The DS-KV6124-WBE1 pushes access-control events over an ISAPI alertStream we
connect out to. Exact field names and how the face snapshot is delivered depend
on the firmware, so this command's job for now is discovery: print a short
summary of every part and, with --dump, write the raw body and headers to a
capture folder. Later phases turn those parts into attendance records.

    python manage.py attendance_listen --dump --max 20

Stop with Ctrl+C. The capture folder holds face images, so it is git-ignored and
should be treated as personal data.
"""
import time

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core import hikvision


class Command(BaseCommand):
    help = ('Domofon (Hikvision) alertStream hodisalarini tinglaydi. '
            '--dump bilan xom hodisalarni faylga yozadi (format aniqlash uchun).')

    def add_arguments(self, parser):
        parser.add_argument('--dump', action='store_true',
                            help='Har bir qism (hodisa/rasm) capture papkasiga yoziladi.')
        parser.add_argument('--max', type=int, default=0,
                            help='Shuncha qism kelgach to‘xtaydi. 0 — cheksiz (standart).')
        parser.add_argument('--path', default='',
                            help='alertStream yo‘lini almashtiradi (standart: HIKVISION_ALERT_PATH).')
        parser.add_argument('--retry', type=float, default=5.0,
                            help='Ulanish uzilganda qayta urinishdan oldingi kutish (soniya).')

    def handle(self, *args, **options):
        try:
            url = hikvision.base_url()
        except hikvision.HikvisionNotConfigured as error:
            raise CommandError(str(error))
        from pathlib import Path
        dump_dir = Path(settings.ATTENDANCE_CAPTURE_DIR)
        if options['dump']:
            dump_dir.mkdir(parents=True, exist_ok=True)
            self.stdout.write(f'Xom hodisalar yoziladi: {dump_dir}')
        path = options['path'] or settings.HIKVISION_ALERT_PATH
        self.stdout.write(f'Ulanmoqda: {url}{path}')
        seen = 0
        while True:
            try:
                for headers, body in hikvision.events(options['path'] or None):
                    seen += 1
                    self.report(seen, headers, body)
                    if options['dump']:
                        self.write_part(dump_dir, seen, headers, body)
                    if options['max'] and seen >= options['max']:
                        self.stdout.write(self.style.SUCCESS(f'{seen} qism olindi. To‘xtatildi.'))
                        return
            except KeyboardInterrupt:
                self.stdout.write('\nTo‘xtatildi.')
                return
            except httpx.HTTPStatusError as error:
                raise CommandError(f'Domofon {error.response.status_code} qaytardi. '
                                   f'Foydalanuvchi/parolni va alertStream yo‘lini tekshiring.')
            except (httpx.HTTPError, ValueError, OSError) as error:
                self.stderr.write(f'Ulanish uzildi ({type(error).__name__}): {error}. '
                                  f'{options["retry"]:.0f}s dan so‘ng qayta urinaman.')
                time.sleep(max(1.0, options['retry']))

    def report(self, index, headers, body):
        content_type = headers.get('content-type', 'noma’lum')
        if hikvision.is_image(headers):
            self.stdout.write(f'#{index} rasm: {content_type}, {len(body)} bayt')
            return
        text = body.decode('utf-8', 'replace').strip()
        self.stdout.write(f'#{index} hodisa: {content_type}, {len(body)} bayt')
        if text:
            self.stdout.write('    ' + text[:800].replace('\n', '\n    '))

    def write_part(self, dump_dir, index, headers, body):
        content_type = headers.get('content-type', '')
        ext = ('jpg' if hikvision.is_image(headers)
               else 'json' if 'json' in content_type
               else 'xml' if 'xml' in content_type else 'bin')
        stamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
        base = f'{stamp}-{index:05d}'
        (dump_dir / f'{base}.{ext}').write_bytes(body)
        (dump_dir / f'{base}.headers.txt').write_text(
            '\n'.join(f'{name}: {value}' for name, value in headers.items()), encoding='utf-8')
