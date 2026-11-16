from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ..management.commands.align_structure import CHART
from ..models import Department, Task, User

ALL_UNITS = [name for units in CHART.values() for name, _ in units]


class AlignStructureTests(TestCase):
    def setUp(self):
        # The state found in production: short names, both deputies over everything.
        names = ['Aktivlar bilan ishlash bo‘limi', 'Boshqaruv administratsiyasi', 'Buxgalteriya va MHXS bo‘limi', 'Devonxona',
                 'Inson resurslari xizmati', 'Kommunikatsiya tarmoqlari loyiha ofisi', 'Loyihalarni moliyalashtirish bo‘limi',
                 'Metodologiya bo‘limi', 'Moliyaviya rivojlantirish va tahlil qilish bo‘limi', 'Qurilish va renovatsiya loyiha ofisi',
                 'Rahbariyat', 'Raqamlashtirish bo‘limi', 'Turizm loyiha ofisi', 'Xo‘jalik ishlari bo‘limi', "Yuridik bo'lim"]
        self.departments = {name: Department.objects.create(name=name) for name in names}
        self.chair = User.objects.create_user('rais', full_name='Sinov Rais', role='chair')
        self.cfo = User.objects.create_user('birinchi', full_name='Sinov Birinchi', role='deputy',
                                            job_title='Iqtisodiyot va moliya bo‘yicha birinchi o‘rinbosar')
        self.cpo = User.objects.create_user('loyiha', full_name='Sinov Loyiha', role='deputy',
                                            job_title='Loyiha boshqaruvi bo‘yicha o‘rinbosar')
        for deputy in (self.cfo, self.cpo):
            deputy.supervised.set(self.departments.values())
        self.lawyer = User.objects.create_user('yurist', full_name='Sinov Yurist',
                                               department=self.departments["Yuridik bo'lim"])
        self.builder = User.objects.create_user('quruvchi', full_name='Sinov Quruvchi',
                                                department=self.departments['Qurilish va renovatsiya loyiha ofisi'])
        Task.objects.create(issuer=self.chair, assignee=self.lawyer, title='Shartnoma')
        Task.objects.create(issuer=self.chair, assignee=self.builder, title='Smeta')

    def run_command(self, *args):
        out = StringIO()
        call_command('align_structure', *args, stdout=out)
        return out.getvalue()

    def supervised(self, deputy):
        return set(deputy.supervised.values_list('name', flat=True))

    def test_without_apply_nothing_is_written(self):
        text = self.run_command()
        self.assertIn('Hech narsa yozilmadi', text)
        self.assertIn('+ yaratiladi: Xaridlar sho‘basi', text)
        self.assertIn('~ nomi o‘zgaradi: «Yuridik bo\'lim» → «Yuridik xizmati»', text)
        self.assertEqual(Department.objects.count(), 15)
        self.assertEqual(len(self.supervised(self.cfo)), 15)
        self.assertTrue(Department.objects.filter(name="Yuridik bo'lim").exists())

    def test_apply_matches_the_chart(self):
        text = self.run_command('--apply')
        self.assertIn('Saqlandi', text)
        # Every unit of the chart exists once, under its chart name; nothing was deleted.
        self.assertEqual(set(ALL_UNITS) - set(Department.objects.values_list('name', flat=True)), set())
        self.assertEqual(Department.objects.count(), 19 + 2)
        self.assertTrue(Department.objects.filter(name__in=['Rahbariyat', 'Xo‘jalik ishlari bo‘limi']).count() == 2)
        self.assertIn('? «Rahbariyat»', text)
        self.assertEqual(self.supervised(self.cfo), {name for name, _ in CHART['cfo']})
        self.assertEqual(self.supervised(self.cpo), {name for name, _ in CHART['cpo']})
        # Renamed, not recreated: the people stay in their department.
        self.lawyer.refresh_from_db()
        self.assertEqual(self.lawyer.department.name, 'Yuridik xizmati')
        self.assertIn('Investitsiya bo‘yicha o‘rinbosar (CIO): tizimda yo‘q', text)

    def test_deputies_keep_only_their_own_lines_tasks(self):
        self.assertEqual(Task.objects.visible_to(self.cfo).count(), 2)
        text = self.run_command('--apply')
        self.assertIn('! ko‘radigan topshiriqlari: 2 → 0', text)
        self.assertEqual(Task.objects.visible_to(User.objects.get(pk=self.cfo.pk)).count(), 0)
        self.assertEqual([task.title for task in Task.objects.visible_to(User.objects.get(pk=self.cpo.pk))], ['Smeta'])

    def test_second_run_changes_nothing(self):
        self.run_command('--apply')
        text = self.run_command('--apply')
        self.assertNotIn('+ yaratiladi', text)
        self.assertNotIn('nomi o‘zgaradi', text)
        self.assertNotIn('olinadi', text)
        self.assertNotIn('qo‘shiladi', text)

    def test_unclear_deputy_must_be_named(self):
        User.objects.filter(pk=self.cpo.pk).update(job_title='Rais o‘rinbosari')
        with self.assertRaises(CommandError) as raised:
            self.run_command()
        self.assertIn('loyiha', str(raised.exception))
        text = self.run_command('--cpo', 'loyiha', '--apply')
        self.assertEqual(self.supervised(self.cpo), {name for name, _ in CHART['cpo']})
        self.assertIn('Sinov Loyiha (loyiha)', text)
        with self.assertRaises(CommandError):
            self.run_command('--cfo', 'yoq-odam')

    def test_deputy_who_heads_a_department_is_pointed_out(self):
        Department.objects.filter(name='Xo‘jalik ishlari bo‘limi').update(head=self.cpo)
        self.assertIn('«Xo‘jalik ishlari bo‘limi» bo‘linmasining boshlig‘i ham qilib belgilangan', self.run_command())

    def test_two_departments_for_one_unit_stop_the_run(self):
        Department.objects.create(name='Yuridik xizmati')
        with self.assertRaises(CommandError):
            self.run_command('--apply')
        self.assertEqual(len(self.supervised(self.cfo)), 15)
