"""Telegram Mini App sign-in and bot messages.

Mini App data is signed with the bot token, so the server can trust who opened
the app without a password. Accounts are matched by the phone number the person
shares with the bot; an unknown number is never granted an account.
"""
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from urllib.parse import parse_qsl

import httpx
from django.conf import settings
from django.utils import timezone

from .models import User

logger = logging.getLogger(__name__)
API = 'https://api.telegram.org/bot'
MAX_AGE = 24 * 3600


def configured():
    return bool(settings.TELEGRAM_BOT_TOKEN)


def digits(phone):
    """Uzbek numbers are written many ways; compare the last nine digits."""
    return re.sub(r'\D', '', phone or '')[-9:]


def verify(init_data):
    """Return the Telegram user dict when init_data really comes from Telegram."""
    if not configured() or not isinstance(init_data, str) or not 10 < len(init_data) < 8000:
        return None
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received = fields.pop('hash', '')
    if not received:
        return None
    check = '\n'.join(f'{key}={fields[key]}' for key in sorted(fields))
    secret = hmac.new(b'WebAppData', settings.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        issued = int(fields.get('auth_date', '0'))
        person = json.loads(fields.get('user', '{}'))
    except (ValueError, TypeError):
        return None
    if timezone.now().timestamp() - issued > MAX_AGE or not isinstance(person.get('id'), int):
        return None
    return person


def account(telegram_id):
    return User.objects.filter(telegram_id=telegram_id, is_active=True).first()


def link(telegram_id, phone):
    """Bind this Telegram account to the employee holding that phone number."""
    number = digits(phone)
    if len(number) != 9:
        return None
    match = next((user for user in User.objects.filter(is_active=True).exclude(phone='')
                  if digits(user.phone) == number), None)
    if not match:
        return None
    User.objects.filter(telegram_id=telegram_id).exclude(pk=match.pk).update(telegram_id=None)
    match.telegram_id = telegram_id
    match.save(update_fields=['telegram_id'])
    return match


_client = None
_client_lock = threading.Lock()


def client():
    """One long-lived connection to Telegram.

    A fresh client per message paid for a DNS lookup and a TLS handshake every
    time, which from this network is most of the wait.
    """
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(timeout=httpx.Timeout(10, connect=5))
        return _client


def call(method, payload):
    if not configured():
        return None
    started = time.monotonic()
    try:
        response = client().post(f'{API}{settings.TELEGRAM_BOT_TOKEN}/{method}', json=payload)
        if response.status_code != 200:
            logger.warning('Telegram %s failed status=%s', method, response.status_code)
        return response.json() if response.headers.get('content-type', '').startswith('application/json') else None
    except (httpx.RequestError, ValueError) as error:
        logger.warning('Telegram %s failed: %s', method, type(error).__name__)
        return None
    finally:
        elapsed = time.monotonic() - started
        if elapsed > 3:
            logger.warning('Telegram %s took %.1fs', method, elapsed)


def open_button(url, label='Topshiriqni ochish'):
    if settings.TELEGRAM_APP_URL and url.startswith('/'):
        return {'inline_keyboard': [[{'text': label, 'web_app': {'url': settings.TELEGRAM_APP_URL + url}}]]}
    return None


def message(user, text, url=None):
    """One bot message to this employee; silent when they never linked Telegram."""
    if not configured() or not user.telegram_id:
        return False
    payload = {'chat_id': user.telegram_id, 'text': text[:3500], 'disable_web_page_preview': True}
    markup = open_button(url) if url else None
    if markup:
        payload['reply_markup'] = markup
    return bool(call('sendMessage', payload))


def notify(notification):
    task = notification.task
    message(notification.user, f'{notification.title}\n{task.code} — {task.title}', task.get_absolute_url())


CONTACT_KEYBOARD = {'keyboard': [[{'text': '📱 Raqamni yuborish', 'request_contact': True}]],
                    'resize_keyboard': True, 'one_time_keyboard': True}


def reply(chat_id, text, markup=None):
    """An answer carried back in the webhook response instead of a new request."""
    payload = {'method': 'sendMessage', 'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}
    if markup:
        payload['reply_markup'] = markup
    return payload


def start_reply(chat_id):
    return reply(chat_id, 'Assalomu alaykum. Tizimga kirish uchun telefon raqamingizni yuboring — '
                          'u ro‘yxatdagi xodim raqami bilan solishtiriladi.', CONTACT_KEYBOARD)


def linked_reply(chat_id, user):
    # One message, not two: the contact keyboard is one-time and goes away by itself.
    return reply(chat_id, f'{user.full_name} sifatida bog‘landingiz. Ilovani oching.',
                 open_button('/', 'Topshiriqlarni ochish'))


def unknown_reply(chat_id):
    return reply(chat_id, 'Bu raqam tizimda topilmadi. Rahbaringizga murojaat qiling: '
                          'raqamingiz xodimlar ro‘yxatiga kiritilishi kerak.', {'remove_keyboard': True})


def handle(update):
    """One webhook update: /start, a shared contact, or anything else.

    The answer is returned, not sent: Telegram delivers it from the webhook
    response body. That drops a round trip to api.telegram.org from every reply,
    and Telegram holds the next update until this one is answered, so a slow
    reply used to delay whatever the person did next as well.
    """
    payload = update.get('message') or {}
    chat = (payload.get('chat') or {}).get('id')
    sender = (payload.get('from') or {}).get('id')
    if not isinstance(chat, int) or not isinstance(sender, int):
        return None
    contact = payload.get('contact') or {}
    if contact:
        # Only the person's own contact card proves the number belongs to them.
        if contact.get('user_id') != sender:
            return reply(chat, 'Faqat o‘z raqamingizni yuboring.')
        user = link(sender, contact.get('phone_number', ''))
        return linked_reply(chat, user) if user else unknown_reply(chat)
    text = (payload.get('text') or '').strip()
    if text.startswith('/start'):
        user = account(sender)
        return linked_reply(chat, user) if user else start_reply(chat)
    return reply(chat, 'Topshiriqlarni ko‘rish uchun /start yuboring.')
