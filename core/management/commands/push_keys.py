"""Print a VAPID key pair for browser push notifications."""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def encode(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


class Command(BaseCommand):
    help = 'Bildirishnomalar uchun VAPID kalit juftini yaratadi (.env ga ko‘chiring).'

    def handle(self, *args, **options):
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_numbers().private_value.to_bytes(32, 'big')
        public = key.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint)
        self.stdout.write('VAPID_PUBLIC_KEY=' + encode(public))
        self.stdout.write('VAPID_PRIVATE_KEY=' + encode(private))
        self.stdout.write('VAPID_SUBJECT=mailto:admin@example.uz')
        self.stdout.write(self.style.SUCCESS(
            'Bu uch qatorni .env ga qo‘ying va serverni qayta ishga tushiring. '
            'Kalitlar almashtirilsa, xodimlar bildirishnomani qaytadan yoqishi kerak.'))
