from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import guide
from .models import Department, Task, User


class GuideContentTests(TestCase):
    def test_every_role_has_an_overview_and_sections(self):
        self.assertEqual(set(guide.ROLES), {value for value, _ in User.Role.choices})
        for role in guide.ROLES:
            self.assertTrue(guide.sections_for(role), role)

    def test_section_ids_are_unique_and_roles_are_real(self):
        ids = [section['id'] for section in guide.SECTIONS]
        self.assertEqual(len(ids), len(set(ids)))
        for section in guide.SECTIONS:
            self.assertTrue(set(section['roles']) <= set(guide.ROLES), section['id'])


class GuidePageTests(TestCase):
    def setUp(self):
        self.employee = User.objects.create_user('xodim', full_name='Sinov Xodim', role='employee', password='x')
        self.chair = User.objects.create_user('rais', full_name='Sinov Rais', role='chair', password='x')

    def test_reader_sees_their_own_role_first(self):
        self.client.force_login(self.employee)
        page = self.client.get(reverse('guide'))
        self.assertContains(page, 'Menga berilgan topshiriqni bajarish')
        self.assertNotContains(page, 'Struktura va xodimlar')        # chair-only section

    def test_another_role_can_be_read(self):
        self.client.force_login(self.employee)
        page = self.client.get(reverse('guide') + '?role=chair')
        self.assertContains(page, 'Struktura va xodimlar')
        self.assertContains(self.client.get(reverse('guide') + '?role=nobody'), 'Menga berilgan topshiriqni bajarish')

    def test_menu_links_to_the_guide(self):
        self.client.force_login(self.employee)
        self.assertContains(self.client.get(reverse('dashboard')), reverse('guide'))


class OnboardingTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='Sinov bo‘limi')
        self.head = User.objects.create_user('boshliq', full_name='Sinov Boshliq', role='head',
                                             department=self.department, password='eski-parol-123')
        self.worker = User.objects.create_user('ishchi', full_name='Sinov Ishchi', role='employee',
                                               department=self.department, password='x')

    def steps(self, user):
        return {step['title']: step['done'] for step in guide.onboarding_steps(user)}

    def test_dashboard_shows_the_checklist_until_closed(self):
        self.client.force_login(self.head)
        self.assertContains(self.client.get(reverse('dashboard')), 'XUSH KELIBSIZ')
        self.client.post(reverse('onboarding_toggle'))
        self.head.refresh_from_db()
        self.assertIsNotNone(self.head.onboarded_at)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'XUSH KELIBSIZ')
        self.client.post(reverse('onboarding_toggle'), {'reopen': '1'})
        self.assertContains(self.client.get(reverse('dashboard')), 'XUSH KELIBSIZ')

    def test_steps_tick_themselves(self):
        steps = self.steps(self.head)
        self.assertFalse(steps['Telegram botni ulang'])
        self.assertFalse(steps['O‘z parolingizni qo‘ying'])
        self.assertFalse(steps['Birinchi topshiriqni bering'])
        self.head.telegram_id = 5001
        self.head.save()
        Task.objects.create(title='Sinov', issuer=self.head, assignee=self.worker, due_at=timezone.now())
        self.client.force_login(self.head)
        self.client.post(reverse('password_change'), {'old_password': 'eski-parol-123',
                                                      'new_password1': 'Yangi-Parol-2026!', 'new_password2': 'Yangi-Parol-2026!'})
        self.head.refresh_from_db()
        steps = self.steps(self.head)
        self.assertTrue(steps['Telegram botni ulang'])
        self.assertTrue(steps['O‘z parolingizni qo‘ying'])
        self.assertTrue(steps['Birinchi topshiriqni bering'])

    def test_employee_gets_their_own_steps(self):
        steps = self.steps(self.worker)
        self.assertNotIn('Birinchi topshiriqni bering', steps)
        self.assertIn('Davomatingizni ko‘ring', steps)
