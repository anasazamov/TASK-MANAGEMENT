from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import reports
from .attendance import request_excuse
from .models import Department, ExcuseRequest, Task, User


class HrFlagTests(TestCase):
    def setUp(self):
        self.hr_department = Department.objects.create(name='Inson resurslari')
        self.other = Department.objects.create(name='Qurilish')
        self.hr = User.objects.create_user('hr', full_name='Sinov Kadrlar', role='head', is_hr=True,
                                           department=self.hr_department, telegram_id=4001, password='x')
        self.plain_head = User.objects.create_user('boshliq', full_name='Sinov Boshliq', role='head',
                                                   department=self.hr_department, password='x')
        self.far_worker = User.objects.create_user('xodim', full_name='Uzoq Xodim', role='employee',
                                                   department=self.other, password='x')
        self.far_head = User.objects.create_user('qurilish', full_name='Qurilish Boshlig‘i', role='head',
                                                 department=self.other)

    def test_hr_sees_every_employee_on_the_attendance_page(self):
        self.client.force_login(self.hr)
        people = [row['user'] for row in self.client.get(reverse('attendance_daily')).context['rows']]
        self.assertIn(self.far_worker, people)
        self.client.force_login(self.plain_head)
        people = [row['user'] for row in self.client.get(reverse('attendance_daily')).context['rows']]
        self.assertNotIn(self.far_worker, people)        # an ordinary head sees only their own

    def test_hr_opens_the_decision_pages_and_an_ordinary_head_does_not(self):
        for name in ['face_reviews', 'excuse_requests', 'face_enrollments']:
            self.client.force_login(self.hr)
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
            self.client.force_login(self.plain_head)
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_hr_decides_a_permission_request(self):
        item = request_excuse(self.far_worker, 'sick', timezone.localdate())
        self.client.force_login(self.hr)
        self.client.post(reverse('excuse_request_action', args=[item.pk]), {'decision': 'approve'})
        item.refresh_from_db()
        self.assertEqual((item.state, item.resolved_by), (ExcuseRequest.State.APPROVED, self.hr))

    @patch('core.telegram.configured', return_value=True)
    @patch('core.telegram.message', return_value=True)
    def test_hr_is_told_of_new_requests(self, message, configured):
        with self.captureOnCommitCallbacks(execute=True):
            request_excuse(self.far_worker, 'sick', timezone.localdate())
        self.assertIn(self.hr, [call.args[0] for call in message.call_args_list])

    def test_hr_statistics_cover_the_whole_organisation(self):
        Task.objects.create(title='Uzoq ish', issuer=self.far_head, assignee=self.far_worker, due_at=timezone.now())
        names = [row['assignee__full_name'] for row in reports.employee_stats(self.hr)]
        self.assertIn('Uzoq Xodim', names)
        names = [row['assignee__full_name'] for row in reports.employee_stats(self.plain_head)]
        self.assertNotIn('Uzoq Xodim', names)

    def test_hr_as_an_employee_still_gets_the_staff_pages(self):
        clerk = User.objects.create_user('kadr', full_name='Sinov Kadr', role='employee', is_hr=True, password='x')
        self.client.force_login(clerk)
        self.assertContains(self.client.get(reverse('dashboard')), reverse('employees'))
        self.assertEqual(self.client.get(reverse('employees')).status_code, 200)
        self.assertContains(self.client.get(reverse('guide')), 'HR: xodimlar davomati va statistikasi')
