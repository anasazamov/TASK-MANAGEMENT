"""The chair's deputy: a reach limited to the departments they answer for.

A deputy sees and watches those departments and gives work inside them. What
they may not do is decide on somebody else's task — accepting execution,
returning it or moving a deadline belongs to whoever issued it.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from .forms import EmployeeEditForm
from .models import Department, Task, User
from .permissions import assignees_for, can_control, can_manage, controllers_for
from .services import create_task, task_action


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class DeputyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.build = Department.objects.create(name='Qurilish')
        cls.money = Department.objects.create(name='Moliya')
        cls.top = Department.objects.create(name='Rahbariyat')
        cls.chair = User.objects.create_user('rais', password='Testing-123!', full_name='Abilov Feruz', role='chair')
        cls.deputy = User.objects.create_user('orinbosar', password='Testing-123!', full_name='Musinov Dilshod',
                                              role='deputy', department=cls.top)
        cls.deputy.supervised.add(cls.build)
        cls.head = User.objects.create_user('boshliq', password='Testing-123!', full_name='Tuyliyev Asliddin',
                                            role='head', department=cls.build)
        cls.worker = User.objects.create_user('xodim', password='Testing-123!', full_name='Xalimov Farrux',
                                              department=cls.build)
        cls.outsider = User.objects.create_user('moliyachi', password='Testing-123!', full_name='Yarmatov Ne’matjon',
                                                department=cls.money)
        cls.mine = create_task(cls.head, dict(title='Qurilish hisoboti', description='',
                                              assignee=cls.worker, due_at=None))
        cls.elsewhere = create_task(cls.chair, dict(title='Moliya hisoboti', description='',
                                                    assignee=cls.outsider, due_at=None))

    def test_the_deputy_sees_their_departments_and_nothing_further(self):
        visible = Task.objects.visible_to(self.deputy)
        self.assertIn(self.mine, visible)
        self.assertNotIn(self.elsewhere, visible)
        # Adding a department widens the view without touching anything else.
        self.deputy.supervised.add(self.money)
        self.assertIn(self.elsewhere, Task.objects.visible_to(self.deputy))

    def test_work_is_given_inside_those_departments_only(self):
        people = assignees_for(self.deputy)
        self.assertEqual({p.pk for p in people}, {self.head.pk, self.worker.pk})
        self.assertNotIn(self.chair, people)
        task = create_task(self.deputy, dict(title='Loyihani tekshir', description='',
                                             assignee=self.head, due_at=None))
        self.assertEqual(task.issuer, self.deputy)
        with self.assertRaises(PermissionDenied):
            create_task(self.deputy, dict(title='Moliya ishi', description='',
                                          assignee=self.outsider, due_at=None))

    def test_watching_comes_with_the_role_but_deciding_does_not(self):
        # No one had to name the deputy a controller on this task.
        self.assertTrue(can_control(self.deputy, self.mine))
        self.assertFalse(can_manage(self.deputy, self.mine))
        task_action(self.deputy, self.mine.pk, 'request_report', 'Haftalik hisobot kerak')
        self.mine.refresh_from_db()
        self.assertIsNotNone(self.mine.report_requested_at)
        task_action(self.worker, self.mine.pk, 'submit', 'Bajarildi')
        for action, text in [('accept', ''), ('return', 'Qaytaraman'),
                             ('set_deadline', ''), ('request_deadline', '')]:
            with self.subTest(action=action), self.assertRaises((PermissionDenied, ValidationError)):
                task_action(self.deputy, self.mine.pk, action, text,
                            timezone.now() + timezone.timedelta(days=3) if 'deadline' in action else None)
        # Outside their departments they are not even a watcher.
        self.assertFalse(can_control(self.deputy, self.elsewhere))

    def test_their_own_task_is_theirs_to_decide(self):
        task = create_task(self.deputy, dict(title='Smetani tekshir', description='',
                                             assignee=self.worker, due_at=None))
        task_action(self.worker, task.pk, 'submit', 'Tayyor')
        task_action(self.deputy, task.pk, 'accept', '')
        task.refresh_from_db()
        self.assertEqual(task.status, 'accepted')

    def test_controllers_offered_to_a_deputy_stay_within_reach(self):
        secretary = User.objects.create_user('kotiba', full_name='Nodira Yusupova', role='secretary')
        people = controllers_for(self.deputy)
        self.assertIn(self.worker, people)
        self.assertIn(secretary, people)
        self.assertNotIn(self.outsider, people)

    def test_the_role_and_its_departments_are_set_together(self):
        form = EmployeeEditForm(instance=self.worker, data={
            'full_name': self.worker.full_name, 'job_title': '', 'phone': '',
            'department': self.build.pk, 'role': 'deputy', 'supervised': []})
        self.assertFalse(form.is_valid())
        self.assertIn('kamida bitta bo‘linma', str(form.errors))
        form = EmployeeEditForm(instance=self.worker, data={
            'full_name': self.worker.full_name, 'job_title': '', 'phone': '',
            'department': self.build.pk, 'role': 'deputy', 'supervised': [self.money.pk]})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.worker.refresh_from_db()
        self.assertEqual([d.pk for d in self.worker.supervised.all()], [self.money.pk])
        # Moved off the role, the departments go with it.
        form = EmployeeEditForm(instance=self.worker, data={
            'full_name': self.worker.full_name, 'job_title': '', 'phone': '',
            'department': self.build.pk, 'role': 'employee', 'supervised': [self.money.pk]})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.supervised.exists())

    def test_editing_something_else_leaves_the_departments_alone(self):
        # The agent edits a name or a job title without ever seeing this field.
        form = EmployeeEditForm({'full_name': self.deputy.full_name, 'job_title': 'Birinchi o‘rinbosar',
                                 'phone': '', 'department': self.top.pk, 'role': 'deputy'},
                                instance=self.deputy)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.deputy.refresh_from_db()
        self.assertEqual([d.pk for d in self.deputy.supervised.all()], [self.build.pk])
        self.assertEqual(self.deputy.job_title, 'Birinchi o‘rinbosar')

    def test_the_employees_page_and_its_statistics_are_scoped(self):
        from . import reports
        self.client.force_login(self.deputy)
        page = self.client.get('/employees/')
        self.assertContains(page, 'Xalimov')
        self.assertNotContains(page, 'Yarmatov')
        names = {row['assignee__full_name'] for row in reports.employee_stats(self.deputy)}
        self.assertIn('Xalimov Farrux', names)
        self.assertNotIn('Yarmatov Ne’matjon', names)
