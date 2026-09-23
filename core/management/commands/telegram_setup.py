"""Point the Telegram bot at this server: webhook, menu button and commands."""
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import telegram


class Command(BaseCommand):
    help = 'Telegram botni shu serverga ulaydi (webhook va «Ochish» tugmasi).'

    def add_arguments(self, parser):
        parser.add_argument('--url', help='Saytning tashqi manzili, masalan https://tasks.example.uz')
        parser.add_argument('--info', action='store_true',
                            help='Hech narsani o‘zgartirmay, hozirgi webhook holatini ko‘rsatadi.')

    def report(self):
        """Telegram's own view of the webhook: why a reply may be arriving late."""
        info = telegram.call('getWebhookInfo', {})
        result = info.get('result') if isinstance(info, dict) else None
        if not result:
            self.stdout.write('Webhook holati o‘qilmadi.')
            return
        self.stdout.write(f"Manzil: {result.get('url') or '(yo‘q)'}")
        self.stdout.write(f"Navbatdagi yangilanishlar: {result.get('pending_update_count', 0)}")
        if result.get('last_error_message'):
            when = datetime.fromtimestamp(result.get('last_error_date', 0), tz=dt_timezone.utc)
            self.stdout.write(self.style.WARNING(
                f"So‘nggi xato ({when:%Y-%m-%d %H:%M} UTC): {result['last_error_message']}"))

    def handle(self, *args, **options):
        base = (options['url'] or settings.TELEGRAM_APP_URL).rstrip('/')
        if not telegram.configured():
            raise CommandError('TELEGRAM_BOT_TOKEN sozlanmagan.')
        if options['info']:
            self.report()
            return
        if not settings.TELEGRAM_WEBHOOK_SECRET:
            raise CommandError('TELEGRAM_WEBHOOK_SECRET sozlanmagan.')
        if not base.startswith('https://'):
            raise CommandError('Telegram faqat https manzil bilan ishlaydi. --url bering yoki TELEGRAM_APP_URL ni to‘ldiring.')
        hook = f'{base}/telegram/webhook/{settings.TELEGRAM_WEBHOOK_SECRET}/'
        results = {
            'setWebhook': telegram.call('setWebhook', {'url': hook, 'allowed_updates': ['message'],
                                                       'drop_pending_updates': True}),
            'setChatMenuButton': telegram.call('setChatMenuButton', {
                'menu_button': {'type': 'web_app', 'text': 'Topshiriqlar', 'web_app': {'url': base + '/'}}}),
            'setMyCommands': telegram.call('setMyCommands', {'commands': [
                {'command': 'start', 'description': 'Hisobni bog‘lash va ilovani ochish'}]}),
        }
        for method, result in results.items():
            ok = isinstance(result, dict) and result.get('ok')
            self.stdout.write(('✓ ' if ok else '✗ ') + method + ('' if ok else ' — bajarilmadi'))
        if not all(isinstance(r, dict) and r.get('ok') for r in results.values()):
            raise CommandError('Telegram sozlamasi to‘liq bajarilmadi. Token va manzilni tekshiring.')
        self.stdout.write(self.style.SUCCESS(f'Bot ulandi: {hook}'))
        self.report()
