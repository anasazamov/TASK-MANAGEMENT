from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from .models import Department, User


class SeedStaffTests(TestCase):
    def seed(self, **options):
        output = StringIO()
        call_command('seed_staff', stdout=output, **options)
        return output.getvalue()

    def test_roster_is_created_with_roles_and_department_heads(self):
        self.seed()
        self.assertEqual(User.objects.count(), 26)
        chair = User.objects.get(username='abilov')
        self.assertEqual((chair.role, chair.department), ('chair', None))
        self.assertEqual(User.objects.get(username='isomiddinov').role, 'office')
        self.assertEqual(User.objects.get(username='tosheva').role, 'secretary')
        self.assertEqual(User.objects.filter(role='head').count(), 13)
        digital = Department.objects.get(name__startswith='Raqamlashtirish')
        self.assertEqual(digital.head.username, 'xudayarov')
        self.assertEqual({u.username for u in digital.employees.all()}, {'xudayarov', 'azamov'})

    def test_household_staff_are_left_out(self):
        self.seed()
        self.assertFalse(Department.objects.filter(name__icontains='xo‘jalik').exists())
        for username in ['narziyev', 'xamrayev', 'axmedova']:
            self.assertFalse(User.objects.filter(username=username).exists())

    def test_every_account_gets_the_shared_starting_password(self):
        output = self.seed()
        self.assertTrue(all(u.check_password('12345678') for u in User.objects.all()))
        self.assertIn('DIQQAT', output)  # A weak shared password is flagged, not silently accepted.
        self.assertIn('Boshlang‘ich parol: 12345678', output)
        with self.assertRaises(CommandError):
            self.seed(password='qisqa')

    def test_accounts_can_be_created_without_a_password(self):
        self.seed(no_password=True)
        self.assertTrue(all(not u.has_usable_password() for u in User.objects.all()))

    def test_rerun_keeps_existing_accounts_and_dry_run_writes_nothing(self):
        self.assertIn('26 ta xodim tayyorlandi (dry-run)', self.seed(dry_run=True))
        self.assertEqual(User.objects.count(), 0)
        self.seed()
        existing = User.objects.get(username='xalimov')
        existing.job_title = 'Boshqa lavozim'
        existing.save(update_fields=['job_title'])
        output = self.seed()
        self.assertIn('Mavjud hisoblar o‘zgartirilmadi', output)
        self.assertEqual(User.objects.count(), 26)
        existing.refresh_from_db()
        self.assertEqual(existing.job_title, 'Boshqa lavozim')
