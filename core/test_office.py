import shutil
import tempfile
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Department, Task, TaskAttachment, User
from .permissions import assignees_for, controllers_for
from .services import add_attachment, create_task, remove_attachment

MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class OfficeAndSecretaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dep, other = Department.objects.create(name='Qurilish'), Department.objects.create(name='IT')
        cls.chair = User.objects.create_user('rais', password='Testing-123!', full_name='Abilov Feruz', role='chair')
        cls.head = User.objects.create_user('boshliq', password='Testing-123!', full_name='Tuyliyev Asliddin', role='head', department=dep)
        cls.other_head = User.objects.create_user('boshliq2', password='Testing-123!', full_name='Yarmatov Nodir', role='head', department=other)
        cls.employee = User.objects.create_user('xodim', password='Testing-123!', full_name='Xalimov Farrux', department=dep)
        cls.office = User.objects.create_user('devon', password='Testing-123!', full_name='Isomiddinov Islom',
                                              role='office', job_title='Devonxona mudiri')
        cls.secretary = User.objects.create_user('kotiba', password='Testing-123!', full_name='Nodira Kotiba',
                                                 role='secretary', job_title='Kotiba')
        cls.task = create_task(cls.head, dict(title='Loyiha pasporti', description='', assignee=cls.employee,
                                              due_at=timezone.now()+timedelta(days=5)))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_office_assigns_letters_to_heads_only(self):
        self.assertEqual({p.pk for p in assignees_for(self.office)}, {self.head.pk, self.other_head.pk})
        letter = create_task(self.office, dict(title='Vazirlik xatini ko‘rib chiqish', description='',
                                               assignee=self.head, due_at=timezone.now()+timedelta(days=3)))
        self.assertEqual(letter.issuer, self.office)
        with self.assertRaises(PermissionDenied):
            create_task(self.office, dict(title='To‘g‘ridan-to‘g‘ri', description='',
                                          assignee=self.employee, due_at=timezone.now()+timedelta(days=3)))
        self.client.force_login(self.office)
        self.assertEqual(self.client.get('/tasks/new/').status_code, 200)
        self.assertEqual(self.client.get('/employees/').status_code, 200)

    def test_office_sees_only_its_own_correspondence(self):
        letter = create_task(self.office, dict(title='Xat', description='', assignee=self.head, due_at=None))
        visible = Task.objects.visible_to(self.office)
        self.assertEqual([t.pk for t in visible], [letter.pk])
        self.client.force_login(self.office)
        self.assertEqual(self.client.get(self.task.get_absolute_url()).status_code, 404)

    def test_secretary_oversees_every_department_and_assigns_across_them(self):
        self.assertTrue(self.secretary.can_oversee and self.secretary.can_assign)
        self.assertIn(self.task, Task.objects.visible_to(self.secretary))
        self.assertEqual({p.pk for p in assignees_for(self.secretary)},
                         {self.head.pk, self.other_head.pk, self.employee.pk, self.office.pk})
        self.client.force_login(self.secretary)
        self.assertEqual(self.client.get('/employees/').status_code, 200)
        page = self.client.get(self.task.get_absolute_url())
        self.assertContains(page, self.task.title)
        self.assertNotContains(page, 'Qabul qilish')  # Another manager's task stays theirs to decide.
        self.assertEqual(self.client.get('/tasks/new/').status_code, 200)
        self.assertEqual(self.client.get('/structure/').status_code, 403)

    def test_secretary_task_reaches_any_department_and_stays_under_her_control(self):
        given = create_task(self.secretary, dict(title='Rais topshirig‘i', description='',
                                                 assignee=self.employee, due_at=timezone.now()+timedelta(days=2)))
        self.assertEqual(given.issuer, self.secretary)
        self.assertIn(given, Task.objects.visible_to(self.employee))
        self.client.force_login(self.secretary)
        self.assertContains(self.client.get(given.get_absolute_url()), 'Haftalik hisobot so‘rash')

    def test_letter_details_are_stored_and_shown(self):
        self.client.force_login(self.office)
        response = self.client.post('/tasks/new/', {
            'title': 'Xat bo‘yicha chora', 'description': 'Javob tayyorlansin', 'assignee': self.head.pk,
            'due_at': timezone.localtime(timezone.now()+timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
            'letter_number': '01-12/345', 'letter_date': '2026-09-15', 'letter_sender': 'Moliya vazirligi'})
        task = Task.objects.get(title='Xat bo‘yicha chora')
        self.assertRedirects(response, task.get_absolute_url())
        self.assertEqual((task.letter_number, task.letter_date, task.letter_sender),
                         ('01-12/345', date(2026, 9, 15), 'Moliya vazirligi'))
        page = self.client.get(task.get_absolute_url())
        for text in ['01-12/345', '15.09.2026', 'Moliya vazirligi']:
            self.assertContains(page, text)

    def file(self, name='xat.pdf', content=b'%PDF-1.4 test'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def test_files_are_attached_downloaded_and_removed_by_their_owner(self):
        attachment = add_attachment(self.employee, self.task.pk, self.file())
        self.assertEqual((attachment.name, attachment.size), ('xat.pdf', 13))
        self.assertTrue(attachment.file.name.startswith(f'tasks/{self.task.pk}/'))
        self.client.force_login(self.employee)
        page = self.client.get(self.task.get_absolute_url())
        self.assertContains(page, 'xat.pdf')
        download = self.client.get(f'/tasks/{self.task.pk}/files/{attachment.pk}/')
        self.assertEqual(b''.join(download.streaming_content), b'%PDF-1.4 test')
        self.client.force_login(self.other_head)
        self.assertEqual(self.client.get(f'/tasks/{self.task.pk}/files/{attachment.pk}/').status_code, 404)
        with self.assertRaises(PermissionDenied):
            remove_attachment(self.other_head, attachment.pk)
        remove_attachment(self.head, attachment.pk)
        self.assertFalse(TaskAttachment.objects.exists())

    def test_unsupported_or_oversized_files_are_refused(self):
        for upload, reason in [(self.file('virus.exe', b'MZ'), 'turi'), (self.file(content=b''), 'bo‘sh')]:
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                add_attachment(self.head, self.task.pk, upload)
        with override_settings(TASK_FILE_MAX_BYTES=5), self.assertRaises(ValidationError):
            add_attachment(self.head, self.task.pk, self.file())
        with self.assertRaises(PermissionDenied):
            add_attachment(self.other_head, self.task.pk, self.file())
        self.assertFalse(TaskAttachment.objects.exists())

    def test_agent_opens_and_downloads_the_employee_report(self):
        from . import agent_tools
        from .models import AgentConversation
        conversation = AgentConversation.objects.create(user=self.head)
        opened = agent_tools.execute(self.head, conversation, 'open_report', {'week': None, 'download': False}, set())
        self.assertEqual(opened['navigation']['url'], '/employees/')
        weekly = agent_tools.execute(self.head, conversation, 'open_report', {'week': '2026-W38', 'download': True}, set())
        self.assertEqual(weekly['navigation']['url'], '/employees/?week=2026-W38&format=xlsx')
        self.assertIn('Excel', weekly['message'])
        with self.assertRaises(ValidationError):
            agent_tools.execute(self.head, conversation, 'open_report', {'week': '2026-W99', 'download': False}, set())
        with self.assertRaises(PermissionDenied):
            agent_tools.execute(self.employee, conversation, 'open_report', {'week': None, 'download': False}, set())

    def test_agent_records_letter_details_on_a_new_task(self):
        from . import agent_tools
        from .models import AgentConversation
        conversation = AgentConversation.objects.create(user=self.office)
        args = agent_tools.NewTask(title='Xat bo‘yicha chora', description='', assignee_id=self.head.pk,
                                   due_at=timezone.localtime(timezone.now()+timedelta(days=2)).isoformat(),
                                   no_deadline=False, parent_id=None, letter_number='01-12/345',
                                   letter_date='2026-09-15', letter_sender='Moliya vazirligi')
        preview = agent_tools.prepare(self.office, conversation, 'prepare_task', args)
        agent_tools.confirm(self.office, conversation, preview['proposal']['id'])
        task = Task.objects.get(title='Xat bo‘yicha chora')
        self.assertEqual((task.letter_number, str(task.letter_date), task.letter_sender),
                         ('01-12/345', '2026-09-15', 'Moliya vazirligi'))
        read = agent_tools.execute(self.office, conversation, 'get_task', {'task_id': task.pk}, set())
        self.assertEqual(read['letter']['number'], '01-12/345')

    def test_controller_candidates_follow_the_manager_scope(self):
        self.assertEqual({p.pk for p in controllers_for(self.head)},
                         {self.employee.pk, self.secretary.pk, self.office.pk})
        self.assertIn(self.other_head, controllers_for(self.chair))
        self.assertEqual(controllers_for(self.employee).count(), 0)
