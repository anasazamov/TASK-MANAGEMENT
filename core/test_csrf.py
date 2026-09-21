from django.test import Client, TestCase, override_settings

from .models import User


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CsrfOriginTests(TestCase):
    def post(self, origin, with_token=True):
        client = Client(enforce_csrf_checks=True, HTTP_HOST='192.168.1.10')
        client.force_login(User.objects.create_user(f'u{User.objects.count()}', full_name='Sinov'))
        client.get('/')
        headers = {'HTTP_ORIGIN': origin}
        if with_token:
            headers['HTTP_X_CSRFTOKEN'] = client.cookies['csrftoken'].value
        return client.post('/agent/state/', data='{}', content_type='application/json', **headers)

    @override_settings(ALLOWED_HOSTS=['*'], CSRF_TRUST_ALL_ORIGINS=False)
    def test_foreign_origin_is_rejected_by_default(self):
        self.assertEqual(self.post('https://tunnel.example').status_code, 403)

    @override_settings(ALLOWED_HOSTS=['*'], CSRF_TRUST_ALL_ORIGINS=True)
    def test_trust_all_accepts_any_origin_but_still_requires_token(self):
        self.assertNotEqual(self.post('https://tunnel.example').status_code, 403)
        self.assertEqual(self.post('https://tunnel.example', with_token=False).status_code, 403)
