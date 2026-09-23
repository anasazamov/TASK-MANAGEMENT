import json
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from io import StringIO

from . import push
from .models import Department, PushSubscription, Task, User
from .services import create_task, task_action

SUBSCRIPTION = {'endpoint': 'https://fcm.googleapis.com/fcm/send/abc', 'keys': {'p256dh': 'key-data', 'auth': 'auth-data'}}


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   VAPID_PUBLIC_KEY='public-test', VAPID_PRIVATE_KEY='private-test',
                   VAPID_SUBJECT='mailto:admin@example.uz',
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PushTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dep = Department.objects.create(name='Qurilish')
        cls.head = User.objects.create_user('boshliq', password='Testing-123!', full_name='Tuyliyev Asliddin', role='head', department=dep)
        cls.employee = User.objects.create_user('xodim', password='Testing-123!', full_name='Xalimov Farrux', department=dep)

    def subscribe(self, payload=None):
        return self.client.post('/push/subscribe/', json.dumps(payload or SUBSCRIPTION), content_type='application/json')

    def test_the_self_test_reports_what_the_push_service_answered(self):
        self.client.force_login(self.employee)
        refused = self.client.post('/push/test/').json()
        self.assertFalse(refused['ok'])
        self.assertIn('obuna emas', refused['message'])
        self.subscribe()
        with patch('core.push.send_one', return_value='') as send:
            accepted = self.client.post('/push/test/').json()
        self.assertTrue(accepted['ok'])
        self.assertEqual(json.loads(send.call_args.args[1])['title'], 'Sinov xabarnomasi')
        with patch('core.push.send_one', return_value='403 \u2014 VAPID kaliti mos emas'):
            failed = self.client.post('/push/test/').json()
        self.assertFalse(failed['ok'])
        self.assertIn('403', failed['message'])
        with override_settings(VAPID_PRIVATE_KEY=''):
            unconfigured = self.client.post('/push/test/').json()
        self.assertIn('VAPID', unconfigured['message'])

    def test_the_bell_is_polled_without_reloading_the_page(self):
        self.client.force_login(self.employee)
        with self.captureOnCommitCallbacks(execute=True):
            task = create_task(self.head, dict(title='Hisobot', description='', assignee=self.employee, due_at=None))
        empty = self.client.get('/notifications/live/').json()
        self.assertEqual(empty['unread'], 1)
        self.assertEqual(empty['items'], [])  # Nothing is sent before the page says what it has.
        first = self.employee.notifications.get()
        fresh = self.client.get(f'/notifications/live/?after={first.pk - 1}').json()
        self.assertEqual([item['id'] for item in fresh['items']], [first.pk])
        self.assertEqual(fresh['items'][0]['task'], task.title)
        self.assertTrue(fresh['items'][0]['unread'])
        self.assertEqual(fresh['active'], 1)
        self.assertEqual(self.client.get(f'/notifications/live/?after={first.pk}').json()['items'], [])
        # Another employee's notifications are never counted here.
        self.client.force_login(self.head)
        self.assertEqual(self.client.get('/notifications/live/').json()['unread'], 0)

    def test_browser_subscribes_and_unsubscribes_itself(self):
        self.assertEqual(self.subscribe().status_code, 302)  # Anonymous users are sent to login.
        self.client.force_login(self.employee)
        self.assertEqual(self.subscribe().json()['status'], 'subscribed')
        self.subscribe()  # The same browser re-subscribing must not duplicate.
        self.assertEqual(PushSubscription.objects.count(), 1)
        for broken in [{'endpoint': 'http://insecure/x', 'keys': SUBSCRIPTION['keys']},
                       {'endpoint': SUBSCRIPTION['endpoint'], 'keys': {'p256dh': ''}}]:
            self.assertEqual(self.subscribe(broken).status_code, 400)
        self.client.post('/push/unsubscribe/', json.dumps({'endpoint': SUBSCRIPTION['endpoint']}),
                         content_type='application/json')
        self.assertFalse(PushSubscription.objects.exists())

    def test_a_new_task_pushes_the_code_and_title_to_the_assignee(self):
        PushSubscription.objects.create(user=self.employee, endpoint=SUBSCRIPTION['endpoint'],
                                        p256dh='key-data', auth='auth-data')
        with patch('core.push.deliver') as deliver, self.captureOnCommitCallbacks(execute=True):
            task = create_task(self.head, dict(title='Hisobot tayyorlash', description='',
                                               assignee=self.employee, due_at=timezone.now()+timedelta(days=2)))
        deliver.assert_called_once()
        subscriptions, payload = deliver.call_args.args
        self.assertEqual([s.endpoint for s in subscriptions], [SUBSCRIPTION['endpoint']])
        data = json.loads(payload)
        self.assertEqual(data['title'], 'Sizga yangi topshiriq berildi')
        self.assertIn(task.code, data['body'])
        self.assertEqual(data['url'], task.get_absolute_url())
        with patch('core.push.deliver') as quiet, self.captureOnCommitCallbacks(execute=True):
            task_action(self.employee, task.pk, 'comment', 'Savol bor')
        quiet.assert_not_called()  # The commenter's own action pushes to the issuer, who has no browser.

    @override_settings(VAPID_PRIVATE_KEY='')
    def test_nothing_is_sent_until_the_keys_are_configured(self):
        PushSubscription.objects.create(user=self.employee, endpoint=SUBSCRIPTION['endpoint'],
                                        p256dh='key-data', auth='auth-data')
        with patch('core.push.deliver') as deliver, self.captureOnCommitCallbacks(execute=True):
            create_task(self.head, dict(title='Ish', description='', assignee=self.employee, due_at=None))
        deliver.assert_not_called()
        self.assertFalse(push.configured())

    def test_dead_endpoints_are_dropped_and_failures_never_raise(self):
        from pywebpush import WebPushException
        subscription = PushSubscription.objects.create(user=self.employee, endpoint=SUBSCRIPTION['endpoint'],
                                                       p256dh='key-data', auth='auth-data')
        class Response:
            status_code = 410
        with patch('pywebpush.webpush', side_effect=WebPushException('gone', response=Response())):
            push.deliver([subscription], '{}')
        self.assertFalse(PushSubscription.objects.exists())

    def test_deadline_reminders_reach_the_browser_and_prune_dead_endpoints(self):
        PushSubscription.objects.create(user=self.employee, endpoint=SUBSCRIPTION['endpoint'],
                                        p256dh='key-data', auth='auth-data')
        task = create_task(self.head, dict(title='Kechikkan ish', description='',
                                           assignee=self.employee, due_at=timezone.now()+timedelta(days=1)))
        Task.objects.filter(pk=task.pk).update(due_at=timezone.now()-timedelta(days=1))
        with patch('core.push.background') as delivery, self.captureOnCommitCallbacks(execute=True):
            call_command('send_reminders', stdout=StringIO())
        titles = [json.loads(call.args[1])['title'] for call in delivery.call_args_list]
        self.assertIn('Topshiriq muddati o‘tdi', titles)
        stale = PushSubscription.objects.create(user=self.head, endpoint='https://fcm.googleapis.com/fcm/send/old',
                                                p256dh='k', auth='a', failed_at=timezone.now()-timedelta(days=31))
        output = StringIO()
        call_command('send_reminders', stdout=output)
        self.assertIn('dead push subscriptions removed', output.getvalue())
        self.assertFalse(PushSubscription.objects.filter(pk=stale.pk).exists())

    def test_delivery_runs_off_the_request_thread(self):
        subscription = PushSubscription.objects.create(user=self.employee, endpoint=SUBSCRIPTION['endpoint'],
                                                       p256dh='key-data', auth='auth-data')
        with patch('core.push.deliver') as deliver:
            push.background([subscription], '{}')
            for _ in range(50):
                if deliver.called:
                    break
                import time
                time.sleep(0.02)
        deliver.assert_called_once()

    def test_worker_and_keys_are_served_for_the_whole_site(self):
        response = self.client.get('/push-worker.js')
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertIn('javascript', response['Content-Type'])
        self.client.force_login(self.employee)
        page = self.client.get('/notifications/')
        self.assertContains(page, 'public-test')
        self.assertContains(page, 'Bildirishnomani yoqish')
        # Every signed-in page carries the config, so the browser can ask once after login.
        dashboard = self.client.get('/')
        self.assertContains(dashboard, 'push-config')
        self.assertContains(dashboard, 'js/push.js')
        self.assertContains(dashboard, f'data-push-user="{self.employee.pk}"')

    def test_key_command_prints_a_usable_pair(self):
        output = StringIO()
        call_command('push_keys', stdout=output)
        lines = dict(line.split('=', 1) for line in output.getvalue().splitlines() if '=' in line)
        self.assertGreater(len(lines['VAPID_PUBLIC_KEY']), 80)
        self.assertGreater(len(lines['VAPID_PRIVATE_KEY']), 40)
        self.assertNotIn('=', lines['VAPID_PUBLIC_KEY'])  # base64url, no padding
