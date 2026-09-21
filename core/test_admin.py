from django.contrib import admin
from django.test import TestCase, override_settings

from .models import AgentConversation, AgentProposal, Department, PushSubscription, Task, TaskParticipant, User, VoiceProfile


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dep = Department.objects.create(name='Qurilish')
        cls.root = User.objects.create_superuser('root', password='Testing-123!', full_name='Administrator')
        cls.head = User.objects.create_user('boshliq', full_name='Tuyliyev Asliddin', role='head',
                                            department=dep, phone='998915471202')
        cls.employee = User.objects.create_user('xodim', full_name='Xalimov Farrux', department=dep)
        cls.task = Task.objects.create(issuer=cls.head, assignee=cls.employee, title='Hisobot', letter_number='01-12/345')
        TaskParticipant.objects.create(task=cls.task, user=cls.head, kind='controller', added_by=cls.head)
        PushSubscription.objects.create(user=cls.employee, endpoint='https://fcm.googleapis.com/x', p256dh='k', auth='a')

    def test_every_operational_model_has_a_working_page(self):
        self.client.force_login(self.root)
        for path in ['user', 'department', 'task', 'taskparticipant', 'taskattachment',
                     'notification', 'event', 'deadlinerequest', 'generatedpage', 'pushsubscription']:
            with self.subTest(model=path):
                self.assertEqual(self.client.get(f'/admin/core/{path}/').status_code, 200)
        page = self.client.get('/admin/core/user/')
        self.assertContains(page, '998915471202')  # Phone and Telegram state are visible.
        self.assertContains(page, 'Telegram')

    def test_workflow_records_cannot_be_edited_by_hand(self):
        self.client.force_login(self.root)
        for path in ['task', 'taskparticipant', 'event', 'notification']:
            with self.subTest(model=path):
                self.assertEqual(self.client.get(f'/admin/core/{path}/add/').status_code, 403)
        response = self.client.get(f'/admin/core/task/{self.task.pk}/change/')
        self.assertNotContains(response, 'name="title"')

    def test_private_records_are_not_exposed(self):
        registered = {model for model in admin.site._registry}
        for model in [AgentConversation, AgentProposal, VoiceProfile]:
            self.assertNotIn(model, registered)

    def test_only_a_superuser_reaches_the_admin(self):
        self.client.force_login(self.head)
        response = self.client.get('/admin/core/user/')
        self.assertIn(response.status_code, (302, 403))
