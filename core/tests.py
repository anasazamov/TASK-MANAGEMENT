from datetime import timedelta
from io import StringIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import EmployeeEditForm
from .models import DeadlineRequest, Department, Event, Notification, Task, User
from .services import create_task, task_action


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dep = Department.objects.create(name='Qurilish')
        cls.other_dep = Department.objects.create(name='IT')
        cls.chair = User.objects.create_user('rais', password='Testing-123!', full_name='Abilov Feruz', role='chair')
        cls.head = User.objects.create_user('boshliq', password='Testing-123!', full_name='Tuyliyev Asliddin', role='head', department=cls.dep)
        cls.employee = User.objects.create_user('xodim', password='Testing-123!', full_name='Xalimov Farrux', department=cls.dep)
        cls.other = User.objects.create_user('other', password='Testing-123!', full_name='Other Employee', department=cls.other_dep)
        cls.dep.head = cls.head
        cls.dep.save()
        cls.parent = create_task(cls.chair, dict(title='Asosiy loyiha', description='Talablar', assignee=cls.head, due_at=timezone.now()+timedelta(days=10)))
        cls.child = create_task(cls.head, dict(title='Loyiha pasportlari', description='', assignee=cls.employee, due_at=timezone.now()+timedelta(days=5)), parent=cls.parent)
        cls.private = create_task(cls.chair, dict(title='Boshqa bo‘lim', description='', assignee=cls.other, due_at=None))

    def login(self, user):
        self.client.force_login(user)

    def test_anonymous_redirects_to_login(self):
        self.assertRedirects(self.client.get('/tasks/'), '/login/?next=/tasks/')

    def test_login_and_logout(self):
        response = self.client.post('/login/', {'username': 'rais', 'password': 'Testing-123!'})
        self.assertRedirects(response, '/')
        self.assertEqual(self.client.get('/logout/').status_code, 405)
        self.assertRedirects(self.client.post('/logout/'), '/login/')

    def test_login_cannot_redirect_offsite(self):
        response = self.client.post('/login/?next=https://evil.example/', {'username': 'rais', 'password': 'Testing-123!'})
        self.assertEqual(response.url, '/')

    def test_role_scopes(self):
        self.assertSetEqual(set(Task.objects.visible_to(self.chair).values_list('pk', flat=True)), {self.parent.pk, self.child.pk, self.private.pk})
        self.assertSetEqual(set(Task.objects.visible_to(self.head).values_list('pk', flat=True)), {self.parent.pk, self.child.pk})
        self.assertSetEqual(set(Task.objects.visible_to(self.employee).values_list('pk', flat=True)), {self.child.pk})

    def test_employee_cannot_read_other_task_or_its_drawer(self):
        self.login(self.employee)
        for path in [self.private.get_absolute_url(), self.private.get_absolute_url()+'?panel=1']:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_employee_cannot_assign(self):
        self.login(self.employee)
        self.assertEqual(self.client.get('/tasks/new/').status_code, 403)
        with self.assertRaises(PermissionDenied):
            create_task(self.employee, dict(title='Forbidden', assignee=self.other, description='', due_at=None))

    def test_head_cannot_assign_outside_department(self):
        with self.assertRaises(PermissionDenied):
            create_task(self.head, dict(title='Forbidden', assignee=self.other, description='', due_at=None))

    def test_parent_scope_and_due_validation(self):
        for due in [None, timezone.now()+timedelta(days=20)]:
            with self.assertRaises(ValidationError):
                create_task(self.head, dict(title='Too late', assignee=self.employee, description='', due_at=due), parent=self.parent)
        with self.assertRaises(PermissionDenied):
            create_task(self.chair, dict(title='Not own parent', assignee=self.other, description='', due_at=None), parent=self.parent)

    def test_past_deadline_rejected(self):
        with self.assertRaises(ValidationError):
            create_task(self.chair, dict(title='Past', assignee=self.head, description='', due_at=timezone.now()-timedelta(days=1)))

    def test_complete_submit_accept_flow(self):
        task_action(self.employee, self.child.pk, 'submit', 'Pasportlar yangilandi.')
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, 'submitted')
        task_action(self.head, self.child.pk, 'accept')
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, 'accepted')
        self.assertIsNotNone(self.child.accepted_at)
        task_action(self.head, self.parent.pk, 'submit', 'Barcha materiallar tayyor.')
        task_action(self.chair, self.parent.pk, 'accept')
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.status, 'accepted')
        self.assertTrue(Event.objects.filter(task=self.parent, kind='accepted', actor=self.chair).exists())
        self.assertTrue(Notification.objects.filter(user=self.employee, task=self.child, title='Ijro qabul qilindi').exists())

    def test_parent_blocked_until_children_accepted(self):
        with self.assertRaises(ValidationError):
            task_action(self.head, self.parent.pk, 'submit', 'Done')
        task_action(self.employee, self.child.pk, 'submit', 'Done')
        with self.assertRaises(ValidationError):
            task_action(self.head, self.parent.pk, 'submit', 'Done')

    def test_self_approval_and_cross_scope_mutation_rejected(self):
        task_action(self.employee, self.child.pk, 'submit', 'Done')
        with self.assertRaises(PermissionDenied):
            task_action(self.employee, self.child.pk, 'accept')
        with self.assertRaises(PermissionDenied):
            task_action(self.other, self.child.pk, 'accept')

    def test_duplicate_submit_and_empty_report_rejected(self):
        with self.assertRaises(ValidationError):
            task_action(self.employee, self.child.pk, 'submit', '   ')
        task_action(self.employee, self.child.pk, 'submit', 'Done')
        with self.assertRaises(ValidationError):
            task_action(self.employee, self.child.pk, 'submit', 'Done again')

    def test_return_requires_reason_and_reopens(self):
        task_action(self.employee, self.child.pk, 'submit', 'Done')
        with self.assertRaises(ValidationError):
            task_action(self.head, self.child.pk, 'return')
        task_action(self.head, self.child.pk, 'return', 'Raqamlar to‘liq emas.')
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, 'active')
        self.assertTrue(self.child.events.filter(kind='returned', body='Raqamlar to‘liq emas.').exists())

    def test_unsubmitted_task_cannot_be_accepted(self):
        with self.assertRaises(ValidationError):
            task_action(self.head, self.child.pk, 'accept')

    def test_closed_task_immutable_except_comments(self):
        task_action(self.employee, self.child.pk, 'submit', 'Done')
        task_action(self.head, self.child.pk, 'accept')
        for action in ['submit', 'set_deadline', 'request_report', 'return']:
            with self.assertRaises(ValidationError):
                task_action(self.head, self.child.pk, action, 'Text', timezone.now()+timedelta(days=6))
        task_action(self.head, self.child.pk, 'comment', 'Rahmat.')
        self.assertTrue(self.child.events.filter(kind='comment').exists())

    def test_deadline_request_and_approval(self):
        due = timezone.now()+timedelta(days=7)
        task_action(self.employee, self.child.pk, 'request_deadline', 'Ekspertiza kechikdi', due)
        with self.assertRaises(ValidationError):
            task_action(self.employee, self.child.pk, 'request_deadline', 'Again', due)
        self.assertEqual(DeadlineRequest.objects.filter(task=self.child, state='pending').count(), 1)
        task_action(self.head, self.child.pk, 'approve_deadline')
        self.child.refresh_from_db()
        self.assertEqual(self.child.due_at, due)
        self.assertEqual(self.child.deadline_requests.get().state, 'approved')
        with self.assertRaises(ValidationError):
            task_action(self.head, self.child.pk, 'approve_deadline')

    def test_extension_cannot_exceed_parent(self):
        with self.assertRaises(ValidationError):
            task_action(self.employee, self.child.pk, 'request_deadline', 'Reason', timezone.now()+timedelta(days=20))

    def test_approval_revalidates_parent_deadline(self):
        due = timezone.now()+timedelta(days=9)
        task_action(self.employee, self.child.pk, 'request_deadline', 'Reason', due)
        task_action(self.chair, self.parent.pk, 'set_deadline', due_at=timezone.now()+timedelta(days=6))
        with self.assertRaises(ValidationError):
            task_action(self.head, self.child.pk, 'approve_deadline')

    def test_parent_cannot_shorten_past_active_child(self):
        with self.assertRaises(ValidationError):
            task_action(self.chair, self.parent.pk, 'set_deadline', due_at=timezone.now()+timedelta(days=2))

    def test_report_request_and_delivery(self):
        task_action(self.head, self.child.pk, 'request_report')
        self.child.refresh_from_db()
        self.assertIn('javob kutilmoqda', self.child.report_label)
        task_action(self.employee, self.child.pk, 'report', 'Haftalik natijalar tayyor.')
        self.child.refresh_from_db()
        self.assertEqual(self.child.report_label, 'Hisobot berilgan')

    def test_csrf_and_post_only(self):
        self.login(self.head)
        url = reverse('task_action', args=[self.child.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.head)
        self.assertEqual(csrf_client.post(url, {'action':'request_report'}).status_code, 403)

    def test_malformed_date_does_not_clear_deadline(self):
        self.login(self.head)
        old_due = self.child.due_at
        self.client.post(reverse('task_action', args=[self.child.pk]), {'action':'set_deadline','due_at':'not-a-date'})
        self.child.refresh_from_db()
        self.assertEqual(old_due, self.child.due_at)

    def test_comments_are_escaped(self):
        task_action(self.employee, self.child.pk, 'comment', '<script>alert(1)</script>')
        self.login(self.employee)
        response = self.client.get(self.child.get_absolute_url())
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_all_pages_render_for_roles(self):
        for user, paths in [
            (self.chair, ['/', '/tasks/', '/employees/', '/structure/', '/employees/new/', '/timeline/', '/chains/', '/notifications/', '/tasks/new/', '/account/password/']),
            (self.head, ['/', '/tasks/', '/employees/', '/timeline/', '/chains/', '/tasks/new/', '/notifications/']),
            (self.employee, ['/', '/tasks/', '/timeline/', '/notifications/']),
        ]:
            self.login(user)
            for path in paths:
                with self.subTest(user=user.username, path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get('/structure/').status_code, 403)

    def test_post_create_through_form(self):
        self.login(self.head)
        response = self.client.post('/tasks/new/', {'title':'Form task', 'description':'Description', 'assignee':self.employee.pk, 'due_at':''})
        task = Task.objects.get(title='Form task')
        self.assertRedirects(response, task.get_absolute_url())
        self.assertEqual(task.issuer, self.head)
        self.assertIsNone(task.due_at)

    def test_filter_search_scope(self):
        self.login(self.employee)
        response = self.client.get('/tasks/?filter=all&q=loyiha')
        self.assertContains(response, 'Loyiha pasportlari')
        self.assertNotContains(response, 'Boshqa bo‘lim')
        self.assertEqual(self.client.get('/tasks/?filter=all&employee='+str(self.other.pk)).context['total'], 0)

    def test_stale_task_appears_in_dashboard_attention(self):
        self.private.created_at = timezone.now()-timedelta(days=15)
        self.private.save()
        self.login(self.chair)
        response = self.client.get('/')
        self.assertIn(self.private.pk, [t.pk for t in response.context['attention']])

    def test_overdue_child_escalates_to_parent_dashboard_and_chain(self):
        self.child.due_at = timezone.now()-timedelta(days=2)
        self.child.save()
        self.login(self.chair)
        response = self.client.get('/')
        self.assertIn(self.parent.pk, [t.pk for t in response.context['attention']])
        self.assertContains(response, 'Eskalatsiya')
        self.assertContains(self.client.get(self.parent.get_absolute_url()), 'Quyi bo‘g‘inda muddat buzildi')
        self.assertContains(self.client.get('/chains/'), 'Quyi zanjirda 1 ta muddat buzilgan')

    def test_elapsed_deadline_days_are_not_rounded_up(self):
        self.child.due_at = timezone.now()-timedelta(days=7, minutes=2)
        self.assertEqual(self.child.deadline_text, '7 kun kechikdi')

    def test_reminders_are_idempotent_and_escalate(self):
        self.child.due_at = timezone.now()-timedelta(days=2)
        self.child.save()
        call_command('send_reminders', stdout=StringIO())
        count = Notification.objects.count()
        event_count = Event.objects.count()
        call_command('send_reminders', stdout=StringIO())
        self.assertEqual(Notification.objects.count(), count)
        self.assertEqual(Event.objects.count(), event_count)
        self.assertTrue(Notification.objects.filter(user=self.chair, title__startswith='Eskalatsiya').exists())

    def test_notification_read_is_owner_scoped(self):
        self.login(self.employee)
        self.client.post('/notifications/read/')
        self.assertFalse(Notification.objects.filter(user=self.employee, read_at__isnull=True).exists())
        self.assertTrue(Notification.objects.filter(user=self.other, read_at__isnull=True).exists())

    def test_blocked_user_cannot_log_in_and_existing_session_expires(self):
        self.login(self.employee)
        self.employee.is_active = False
        self.employee.save()
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertFalse(self.client.login(username='xodim', password='Testing-123!'))

    def test_non_chair_cannot_manage_employees(self):
        self.login(self.head)
        for path in ['/structure/', '/employees/new/', f'/employees/{self.employee.pk}/edit/', f'/employees/{self.employee.pk}/password/']:
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post(f'/employees/{self.employee.pk}/toggle/').status_code, 403)

    def test_employee_transfer_with_active_tasks_rejected(self):
        form = EmployeeEditForm({'full_name':'New name','job_title':'Specialist','role':'employee','department':self.other_dep.pk}, instance=self.employee)
        self.assertFalse(form.is_valid())

    def test_chair_cannot_be_blocked_in_employee_ui(self):
        self.login(self.chair)
        self.assertEqual(self.client.post(f'/employees/{self.chair.pk}/toggle/').status_code, 404)

    def test_chair_can_provision_employee_with_hashed_password(self):
        self.login(self.chair)
        response = self.client.post('/employees/new/', {
            'username': 'new.employee', 'full_name': 'Yangi Xodim', 'job_title': 'Mutaxassis',
            'role': 'employee', 'department': self.dep.pk, 'password1': 'Random-Test-4517!', 'password2': 'Random-Test-4517!',
        })
        self.assertRedirects(response, '/structure/')
        new_user = User.objects.get(username='new.employee')
        self.assertTrue(new_user.check_password('Random-Test-4517!'))
        self.assertNotEqual(new_user.password, 'Random-Test-4517!')
        self.assertFalse(new_user.is_staff)

    def test_chair_cannot_add_second_department_head(self):
        self.login(self.chair)
        response = self.client.post('/employees/new/', {
            'username': 'second.head', 'full_name': 'Boshqa Rahbar', 'job_title': 'Rahbar',
            'role': 'head', 'department': self.dep.pk, 'password1': 'Random-Test-4517!', 'password2': 'Random-Test-4517!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='second.head').exists())

    def test_account_password_change_invalidates_other_sessions(self):
        self.login(self.employee)
        second = Client()
        second.force_login(self.employee)
        response = self.client.post('/account/password/', {
            'old_password':'Testing-123!', 'new_password1':'Changed-4567!', 'new_password2':'Changed-4567!',
        })
        self.assertRedirects(response, '/')
        self.assertEqual(second.get('/').status_code, 302)
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.check_password('Changed-4567!'))

    def test_invalid_parent_id_returns_404(self):
        self.login(self.head)
        self.assertEqual(self.client.get('/tasks/new/?parent=invalid').status_code, 404)
