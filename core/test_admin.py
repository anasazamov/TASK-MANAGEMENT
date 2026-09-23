import mimetypes

from django.contrib import admin
from django.test import TestCase, override_settings

from .models import (AgentConversation, AgentProposal, Department, Event, Notification,
                     PushSubscription, Task, TaskParticipant, User, VoiceProfile)


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

    def test_a_task_can_be_deleted_with_everything_that_hangs_off_it(self):
        Event.objects.create(task=self.task, actor=self.head, kind='comment', body='Izoh')
        Notification.objects.create(task=self.task, user=self.employee, title='Yangi topshiriq')
        self.client.force_login(self.root)
        page = self.client.get(f'/admin/core/task/{self.task.pk}/delete/')
        self.assertEqual(page.status_code, 200)
        self.client.post(f'/admin/core/task/{self.task.pk}/delete/', {'post': 'yes'})
        self.assertFalse(Task.objects.filter(pk=self.task.pk).exists())
        self.assertFalse(Event.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(TaskParticipant.objects.exists())

    def test_history_cannot_be_deleted_on_its_own_page(self):
        event = Event.objects.create(task=self.task, actor=self.head, kind='comment', body='Izoh')
        self.client.force_login(self.root)
        self.assertEqual(self.client.get(f'/admin/core/event/{event.pk}/delete/').status_code, 403)
        self.client.post(f'/admin/core/event/{event.pk}/delete/', {'post': 'yes'})
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_a_task_carrying_sub_tasks_is_refused(self):
        child = Task.objects.create(issuer=self.employee, assignee=self.head, title='Quyi', parent=self.task)
        self.client.force_login(self.root)
        self.client.post(f'/admin/core/task/{self.task.pk}/delete/', {'post': 'yes'})
        self.assertTrue(Task.objects.filter(pk=self.task.pk).exists())
        child.delete()

    def test_module_and_wasm_files_keep_the_type_the_browser_demands(self):
        # A server without these registered hands out application/octet-stream,
        # and Chrome then refuses the speech filter's module and binary.
        self.assertEqual(mimetypes.guess_type('ort-wasm-simd-threaded.mjs')[0], 'text/javascript')
        self.assertEqual(mimetypes.guess_type('ort-wasm-simd-threaded.wasm')[0], 'application/wasm')

    def test_private_records_are_not_exposed(self):
        registered = {model for model in admin.site._registry}
        for model in [AgentConversation, AgentProposal, VoiceProfile]:
            self.assertNotIn(model, registered)

    def test_only_a_superuser_reaches_the_admin(self):
        self.client.force_login(self.head)
        response = self.client.get('/admin/core/user/')
        self.assertIn(response.status_code, (302, 403))
