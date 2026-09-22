from unittest.mock import patch

from django.core.cache import cache
from django.db import OperationalError
from django.test import TestCase, override_settings

from .models import User


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ProductionReadinessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('xodim', password='Testing-123!', full_name='Xalimov Farrux')

    def setUp(self):
        cache.clear()

    def test_health_check_answers_without_a_session(self):
        response = self.client.get('/healthz/')
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(self.client.post('/healthz/').status_code, 405)

    def test_health_check_reports_a_lost_database(self):
        with patch('core.views.connection.ensure_connection', side_effect=OperationalError('down')):
            response = self.client.get('/healthz/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'database_unavailable')

    def test_password_guessing_is_throttled_then_released(self):
        for _ in range(10):
            self.client.post('/login/', {'username': 'xodim', 'password': 'wrong'})
        blocked = self.client.post('/login/', {'username': 'xodim', 'password': 'Testing-123!'})
        self.assertContains(blocked, 'Juda ko‘p urinish')
        self.assertNotIn('_auth_user_id', self.client.session)
        cache.clear()  # The window passes.
        self.assertRedirects(self.client.post('/login/', {'username': 'xodim', 'password': 'Testing-123!'}), '/')

    def test_a_successful_login_clears_earlier_failures(self):
        for _ in range(3):
            self.client.post('/login/', {'username': 'xodim', 'password': 'wrong'})
        self.client.post('/login/', {'username': 'xodim', 'password': 'Testing-123!'})
        self.client.logout()
        for _ in range(9):
            self.client.post('/login/', {'username': 'xodim', 'password': 'wrong'})
        self.assertRedirects(self.client.post('/login/', {'username': 'xodim', 'password': 'Testing-123!'}), '/')

    def test_other_accounts_are_not_locked_out_by_one_target(self):
        User.objects.create_user('boshqa', password='Testing-123!', full_name='Boshqa Xodim')
        for _ in range(10):
            self.client.post('/login/', {'username': 'xodim', 'password': 'wrong'})
        self.assertRedirects(self.client.post('/login/', {'username': 'boshqa', 'password': 'Testing-123!'}), '/')
