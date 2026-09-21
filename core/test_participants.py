from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Department, Event, Notification, Task, TaskParticipant, User
from .services import add_participant, create_task, part_action, task_action


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ParticipantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dep, other_dep = Department.objects.create(name='Qurilish'), Department.objects.create(name='IT')
        cls.chair = User.objects.create_user('rais', password='Testing-123!', full_name='Abilov Feruz', role='chair')
        cls.head = User.objects.create_user('boshliq', password='Testing-123!', full_name='Tuyliyev Asliddin', role='head', department=dep)
        cls.employee = User.objects.create_user('xodim', password='Testing-123!', full_name='Xalimov Farrux', department=dep)
        cls.helper = User.objects.create_user('yordamchi', password='Testing-123!', full_name='Kamolov Akmal', department=dep)
        cls.outsider = User.objects.create_user('ozga', password='Testing-123!', full_name='Maxfiy Xodim', department=other_dep)
        cls.task = create_task(cls.head, dict(title='Loyiha pasporti', description='', assignee=cls.employee,
                                              due_at=timezone.now()+timedelta(days=5)))

    def add(self, user=None, person=None, part='Smeta hisob-kitobi'):
        return add_participant(user or self.head, self.task.pk, (person or self.helper).pk, part)

    def test_added_participant_sees_the_task_and_gets_notified(self):
        self.assertNotIn(self.task, Task.objects.visible_to(self.helper))
        participant = self.add()
        self.assertEqual(participant.status, 'active')
        self.assertIn(self.task, Task.objects.visible_to(self.helper))
        self.assertTrue(Notification.objects.filter(user=self.helper, task=self.task).exists())
        self.assertTrue(Event.objects.filter(task=self.task, kind='part').exists())
        self.client.force_login(self.helper)
        page = self.client.get(self.task.get_absolute_url())
        self.assertContains(page, 'Smeta hisob-kitobi')
        self.assertContains(page, 'Qismni topshirish')
        self.assertEqual(Task.objects.visible_to(self.helper).filter(pk=self.task.pk).count(), 1)
        self.client.force_login(self.head)
        manager_page = self.client.get(self.task.get_absolute_url())
        self.assertContains(manager_page, 'Qo‘shimcha ijrochi qo‘shish')
        self.assertContains(manager_page, self.helper.full_name)
        self.client.force_login(self.employee)
        self.assertNotContains(self.client.get(self.task.get_absolute_url()), 'Qo‘shimcha ijrochi qo‘shish')

    def test_only_the_manager_adds_from_their_own_scope(self):
        for user in [self.employee, self.helper, self.outsider]:
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                self.add(user=user)
        for person, reason in [(self.outsider, 'boshqa bo‘lim'), (self.employee, 'asosiy ijrochi'), (self.head, 'topshiriq bergan')]:
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                self.add(person=person)
        self.add()
        with self.assertRaises(ValidationError):
            self.add()
        with self.assertRaises(ValidationError):
            self.add(person=self.chair, part='Nazorat')
        self.assertEqual(TaskParticipant.objects.count(), 1)

    def test_participant_works_only_on_their_own_part(self):
        participant = self.add()
        task_action(self.helper, self.task.pk, 'comment', 'Savol bor')
        task_action(self.helper, self.task.pk, 'report', 'Haftalik holat')
        for action in ['submit', 'accept', 'set_deadline']:
            with self.subTest(action=action), self.assertRaises(PermissionDenied):
                task_action(self.helper, self.task.pk, action, 'matn')
        with self.assertRaises(PermissionDenied):
            part_action(self.employee, participant.pk, 'submit_part', 'Men emas')
        with self.assertRaises(ValidationError):
            part_action(self.helper, participant.pk, 'submit_part', '')
        part_action(self.helper, participant.pk, 'submit_part', 'Smeta tayyor')
        participant.refresh_from_db()
        self.assertEqual(participant.status, 'submitted')
        self.assertIsNotNone(participant.submitted_at)
        self.assertTrue(Notification.objects.filter(user=self.head, title__contains='qismini topshirdi').exists())

    def test_main_task_waits_for_parts_and_manager_decides(self):
        participant = self.add()
        with self.assertRaises(ValidationError):
            task_action(self.employee, self.task.pk, 'submit', 'Hammasi tayyor')
        part_action(self.helper, participant.pk, 'submit_part', 'Smeta tayyor')
        with self.assertRaises(PermissionDenied):
            part_action(self.helper, participant.pk, 'accept_part')
        part_action(self.head, participant.pk, 'return_part', 'Raqamlar noto‘g‘ri')
        participant.refresh_from_db()
        self.assertEqual((participant.status, participant.submitted_at), ('active', None))
        part_action(self.helper, participant.pk, 'submit_part', 'Tuzatildi')
        part_action(self.head, participant.pk, 'accept_part')
        participant.refresh_from_db()
        self.assertEqual(participant.status, 'accepted')
        task_action(self.employee, self.task.pk, 'submit', 'Hammasi tayyor')
        self.assertEqual(Task.objects.get(pk=self.task.pk).status, 'submitted')

    def test_pages_and_removal_go_through_permission_checks(self):
        participant = self.add()
        self.client.force_login(self.helper)
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/participants/add/',
                                          {'person': self.outsider.pk, 'part': 'Boshqa ish'}).status_code, 302)
        self.assertEqual(TaskParticipant.objects.count(), 1)
        self.client.post(f'/tasks/{self.task.pk}/participants/{participant.pk}/',
                         {'action': 'submit_part', 'text': 'Qismim tayyor'})
        participant.refresh_from_db()
        self.assertEqual(participant.status, 'submitted')
        self.client.force_login(self.head)
        self.client.post(f'/tasks/{self.task.pk}/participants/{participant.pk}/', {'action': 'remove'})
        self.assertFalse(TaskParticipant.objects.exists())
        self.assertNotIn(self.task, Task.objects.visible_to(self.helper))
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(f'/tasks/{self.task.pk}/participants/add/',
                                          {'person': self.helper.pk, 'part': 'Ish'}).status_code, 404)

    def test_agent_reads_participants_from_the_task(self):
        from . import agent_replies, agent_tools
        self.add()
        data = agent_tools.execute(self.head, None, 'get_task', {'task_id': self.task.pk}, set())
        self.assertEqual(data['participants'][0]['part'], 'Smeta hisob-kitobi')
        message = agent_replies.render('get_task', data, 'assignee')
        self.assertIn(self.helper.full_name, message)
        self.assertIn('Smeta hisob-kitobi', message)
