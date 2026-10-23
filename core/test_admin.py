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

    def test_every_record_can_be_added_and_edited(self):
        self.client.force_login(self.root)
        for path in ['task', 'taskparticipant', 'event', 'notification', 'deadlinerequest', 'agentconversation',
                     'agentproposal', 'voiceprofile']:
            with self.subTest(model=path):
                self.assertEqual(self.client.get(f'/admin/core/{path}/add/').status_code, 200)
        response = self.client.get(f'/admin/core/task/{self.task.pk}/change/')
        self.assertContains(response, 'name="title"')

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

    def test_history_can_be_deleted_on_its_own_page(self):
        event = Event.objects.create(task=self.task, actor=self.head, kind='comment', body='Izoh')
        self.client.force_login(self.root)
        self.client.post(f'/admin/core/event/{event.pk}/delete/', {'post': 'yes'})
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_a_task_goes_with_its_sub_tasks(self):
        child = Task.objects.create(issuer=self.employee, assignee=self.head, title='Quyi', parent=self.task)
        self.client.force_login(self.root)
        self.client.post(f'/admin/core/task/{self.task.pk}/delete/', {'post': 'yes'})
        self.assertFalse(Task.objects.filter(pk__in=[self.task.pk, child.pk]).exists())

    def test_deleting_a_user_keeps_the_history_they_wrote(self):
        other = User.objects.create_user('yordamchi', full_name='Sinov Yordamchi')
        event = Event.objects.create(task=self.task, actor=other, kind='comment', body='Izoh')
        self.client.force_login(self.root)
        self.client.post(f'/admin/core/user/{other.pk}/delete/', {'post': 'yes'})
        self.assertFalse(User.objects.filter(pk=other.pk).exists())
        event.refresh_from_db()
        self.assertIsNone(event.actor)

    def test_module_and_wasm_files_keep_the_type_the_browser_demands(self):
        # A server without these registered hands out application/octet-stream,
        # and Chrome then refuses the speech filter's module and binary.
        self.assertEqual(mimetypes.guess_type('ort-wasm-simd-threaded.mjs')[0], 'text/javascript')
        self.assertEqual(mimetypes.guess_type('ort-wasm-simd-threaded.wasm')[0], 'application/wasm')

    def test_every_model_is_in_the_admin(self):
        from django.apps import apps
        registered = set(admin.site._registry)
        for model in apps.get_app_config('core').get_models():
            self.assertIn(model, registered, model.__name__)

    def test_only_a_superuser_reaches_the_admin(self):
        self.client.force_login(self.head)
        response = self.client.get('/admin/core/user/')
        self.assertIn(response.status_code, (302, 403))


class AdministratorIsNotStaffTests(TestCase):
    def test_superuser_stays_out_of_staff_lists(self):
        from .attendance import visible_employees
        from .permissions import assignees_for, controllers_for
        root = User.objects.create_superuser('admin', password='Testing-123!')
        chair = User.objects.create_user('rais', full_name='Sinov Rais', role='chair')
        worker = User.objects.create_user('xodim', full_name='Sinov Xodim')
        self.assertNotIn(root, visible_employees(chair))
        self.assertNotIn(root, assignees_for(chair))
        self.assertNotIn(root, controllers_for(chair))
        self.assertIn(worker, visible_employees(chair))
