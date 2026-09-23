import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from django.test import TestCase, override_settings
from django.utils import timezone

from . import telegram
from .models import Department, User
from .services import create_task

TOKEN = '123456:test-bot-token'
SECRET = 'webhook-secret-path'


def init_data(user_id=555, name='Xalimov', age=0, token=TOKEN, **extra):
    fields = {'auth_date': str(int(timezone.now().timestamp()) - age),
              'query_id': 'AAF', 'user': json.dumps({'id': user_id, 'first_name': name}), **extra}
    check = '\n'.join(f'{key}={fields[key]}' for key in sorted(fields))
    key = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@override_settings(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_BOT_USERNAME='ijro_bot', TELEGRAM_WEBHOOK_SECRET=SECRET,
                   TELEGRAM_APP_URL='https://tasks.example.uz',
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class TelegramTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dep = Department.objects.create(name='Qurilish')
        cls.head = User.objects.create_user('boshliq', password='Testing-123!', full_name='Tuyliyev Asliddin',
                                            role='head', department=dep, phone='998915471202')
        cls.employee = User.objects.create_user('xodim', password='Testing-123!', full_name='Xalimov Farrux',
                                                department=dep, phone='+998 (97) 924-52-50')

    def webhook(self, payload):
        return self.client.post(f'/telegram/webhook/{SECRET}/', json.dumps(payload), content_type='application/json')

    def contact(self, phone, sender=555, owner=None):
        return {'message': {'chat': {'id': sender}, 'from': {'id': sender},
                            'contact': {'phone_number': phone, 'user_id': owner if owner is not None else sender}}}

    def test_only_telegram_signed_data_signs_a_user_in(self):
        User.objects.filter(pk=self.employee.pk).update(telegram_id=555)
        with patch('core.telegram.call'):
            response = self.client.post('/telegram/login/', json.dumps({'init_data': init_data()}),
                                        content_type='application/json')
        self.assertEqual(response.json()['name'], self.employee.full_name)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.employee.pk))
        self.client.logout()
        for broken, reason in [(init_data(token='999:other-bot'), 'boshqa bot'),
                               (init_data(age=25*3600), 'eskirgan'),
                               (init_data()[:-4] + 'aaaa', 'buzilgan hash')]:
            with self.subTest(reason=reason):
                refused = self.client.post('/telegram/login/', json.dumps({'init_data': broken}),
                                           content_type='application/json')
                self.assertEqual(refused.status_code, 403)
                self.assertNotIn('_auth_user_id', self.client.session)

    def test_reopening_after_a_relink_switches_the_account(self):
        User.objects.filter(pk=self.head.pk).update(telegram_id=555)
        self.client.post('/telegram/login/', json.dumps({'init_data': init_data()}), content_type='application/json')
        self.assertEqual(self.client.session['_auth_user_id'], str(self.head.pk))
        # The phone moves to another employee, so the same Telegram is now theirs.
        User.objects.filter(pk=self.head.pk).update(telegram_id=None)
        User.objects.filter(pk=self.employee.pk).update(telegram_id=555)
        response = self.client.post('/telegram/login/', json.dumps({'init_data': init_data()}),
                                    content_type='application/json')
        self.assertTrue(response.json()['switched'])
        self.assertEqual(self.client.session['_auth_user_id'], str(self.employee.pk))
        self.assertEqual(self.client.session['telegram_id'], 555)
        page = self.client.get('/')
        self.assertContains(page, 'data-session-telegram="555"')

    def test_a_session_left_by_an_unlinked_telegram_account_is_dropped(self):
        User.objects.filter(pk=self.employee.pk).update(telegram_id=555)
        self.client.post('/telegram/login/', json.dumps({'init_data': init_data()}), content_type='application/json')
        User.objects.filter(pk=self.employee.pk).update(telegram_id=None)
        response = self.client.post('/telegram/login/', json.dumps({'init_data': init_data()}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_a_password_session_is_kept_when_telegram_is_not_linked(self):
        self.client.force_login(self.head)
        response = self.client.post('/telegram/login/', json.dumps({'init_data': init_data(user_id=900)}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.head.pk))

    def test_unlinked_telegram_account_is_refused_with_the_bot_name(self):
        response = self.client.post('/telegram/login/', json.dumps({'init_data': init_data(user_id=777)}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['bot'], 'ijro_bot')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_sharing_a_known_phone_links_the_account(self):
        with patch('core.telegram.call') as call:
            answer = self.webhook(self.contact('+998 97 924 52 50')).json()
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.telegram_id, 555)
        # Answered inside the webhook response: nothing is sent to Telegram.
        call.assert_not_called()
        self.assertEqual((answer['method'], answer['chat_id']), ('sendMessage', 555))
        self.assertIn('Xalimov', answer['text'])
        self.assertEqual(answer['reply_markup']['inline_keyboard'][0][0]['web_app']['url'],
                         'https://tasks.example.uz/')
        refusal = self.webhook(self.contact('998901111111', sender=556)).json()
        self.assertIn('topilmadi', refusal['text'])
        self.assertEqual(User.objects.filter(telegram_id=556).count(), 0)

    def test_start_is_answered_by_the_webhook_response(self):
        with patch('core.telegram.call') as call:
            answer = self.webhook({'message': {'chat': {'id': 555}, 'from': {'id': 555}, 'text': '/start login'}}).json()
        call.assert_not_called()
        self.assertEqual(answer['method'], 'sendMessage')
        self.assertTrue(answer['reply_markup']['keyboard'][0][0]['request_contact'])
        User.objects.filter(pk=self.employee.pk).update(telegram_id=555)
        linked = self.webhook({'message': {'chat': {'id': 555}, 'from': {'id': 555}, 'text': '/start'}}).json()
        self.assertIn(self.employee.full_name, linked['text'])

    def test_a_contact_belonging_to_somebody_else_is_rejected(self):
        answer = self.webhook(self.contact('998979245250', sender=900, owner=555)).json()
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.telegram_id)
        self.assertIn('o‘z raqamingizni', answer['text'])

    def test_webhook_needs_the_secret_path(self):
        with patch('core.telegram.handle') as handler:
            self.assertEqual(self.client.post('/telegram/webhook/wrong/', '{}', content_type='application/json').status_code, 404)
        handler.assert_not_called()

    def test_moving_telegram_to_another_employee_leaves_one_link(self):
        User.objects.filter(pk=self.head.pk).update(telegram_id=555)
        self.webhook(self.contact('998979245250'))
        self.head.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertIsNone(self.head.telegram_id)
        self.assertEqual(self.employee.telegram_id, 555)

    def test_a_new_task_messages_the_assignee_with_an_open_button(self):
        User.objects.filter(pk=self.employee.pk).update(telegram_id=555)
        with patch('core.telegram.call') as call, self.captureOnCommitCallbacks(execute=True):
            task = create_task(self.head, dict(title='Hisobot tayyorlash', description='',
                                               assignee=self.employee, due_at=timezone.now()+timedelta(days=2)))
        payload = call.call_args.args[1]
        self.assertEqual(payload['chat_id'], 555)
        self.assertIn(task.code, payload['text'])
        button = payload['reply_markup']['inline_keyboard'][0][0]
        self.assertEqual(button['web_app']['url'], 'https://tasks.example.uz' + task.get_absolute_url())

    def test_nothing_is_sent_without_a_token_or_a_link(self):
        with override_settings(TELEGRAM_BOT_TOKEN=''), patch('core.telegram.call') as call, \
                self.captureOnCommitCallbacks(execute=True):
            create_task(self.head, dict(title='Ish', description='', assignee=self.employee, due_at=None))
        call.assert_not_called()
        with patch('core.telegram.call') as call, self.captureOnCommitCallbacks(execute=True):
            create_task(self.head, dict(title='Boshqa ish', description='', assignee=self.employee, due_at=None))
        call.assert_not_called()  # The employee never linked Telegram.

    def test_phone_numbers_match_however_they_are_written(self):
        self.assertEqual(telegram.digits('+998 (97) 924-52-50'), '979245250')
        self.assertEqual(telegram.digits('97 924 52 50'), '979245250')
        self.assertIsNone(telegram.link(555, '12345'))
