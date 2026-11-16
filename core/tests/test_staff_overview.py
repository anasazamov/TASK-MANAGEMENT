import json
import tempfile
from datetime import timedelta
from io import StringIO

from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ..assistant import agent_replies, agent_tools as tools
from ..mcp.server import tools as mcp_tools
from ..models import AgentConversation, Attendance, Department, Task, User


@override_settings(ATTENDANCE_EXEMPT_ROLES=['chair'], ATTENDANCE_HIDDEN_DAYS=set(), ATTENDANCE_WEEKEND_DAYS=set(),
                   ATTENDANCE_ABSENT_HOUR=0, TELEGRAM_BOT_TOKEN='')
class StaffOverviewTests(TestCase):
    def setUp(self):
        self.legal = Department.objects.create(name='Yuridik xizmati')
        self.build = Department.objects.create(name='Qurilish loyiha ofisi')
        make = User.objects.create_user
        self.chair = make('rais', full_name='Sinov Rais', role='chair')
        self.lawyer = make('yurist', full_name='Sinov Yurist', department=self.legal, job_title='Bosh yuristkonsult',
                           expertise='Ma’lumoti: oliy. Mutaxassisligi: huquqshunos. Shartnomalar bo‘yicha 10 yil.')
        self.junior = make('kichik', full_name='Sinov Kichik', department=self.legal, job_title='Mutaxassis')
        self.builder = make('quruvchi', full_name='Sinov Quruvchi', department=self.build, role='head')
        now = timezone.now()
        Task.objects.create(issuer=self.chair, assignee=self.lawyer, title='Faol', due_at=now + timedelta(days=2))
        Task.objects.create(issuer=self.chair, assignee=self.lawyer, title='Kechikkan', due_at=now - timedelta(days=2))
        Task.objects.create(issuer=self.chair, assignee=self.junior, title='Yopilgan', status='accepted')
        Attendance.objects.create(user=self.junior, date=timezone.localdate(), first_entry_at=now, last_entry_at=now,
                                  entry_count=1)

    def overview(self, user, department_id=None):
        return tools.execute(user, AgentConversation.objects.create(user=user), 'staff_overview',
                             {'department_id': department_id}, set())

    def test_chair_sees_fit_load_and_presence_together(self):
        data = self.overview(self.chair)
        rows = {row['name']: row for row in data['people']}
        self.assertEqual(set(rows), {'Sinov Yurist', 'Sinov Kichik', 'Sinov Quruvchi'})
        lawyer = rows['Sinov Yurist']
        self.assertEqual((lawyer['in_progress'], lawyer['overdue'], lawyer['today']), (1, 1, 'absent'))
        self.assertIn('huquqshunos', lawyer['expertise'])
        junior = rows['Sinov Kichik']
        self.assertEqual((junior['in_progress'], junior['expertise']), (0, ''))
        self.assertIn(junior['today'], ('present', 'late'))       # depends on the hour the test runs
        text = agent_replies.render('staff_overview', data)
        self.assertIn('Sinov Yurist — Bosh yuristkonsult, Yuridik xizmati [Xodim]: jarayonda 1 ta, muddati o‘tgan 1 ta; bugun: kelmagan', text)

    def test_place_in_the_company_and_who_is_above(self):
        deputy = User.objects.create_user('orinbosar', full_name='Sinov Orinbosar', role='deputy', job_title='O‘rinbosar (CPO)')
        deputy.supervised.set([self.build])
        self.build.head = self.builder
        self.build.save()
        worker = User.objects.create_user('usta', full_name='Sinov Usta', department=self.build)
        rows = {row['name']: row for row in self.overview(self.chair)['people']}
        self.assertEqual((rows['Sinov Orinbosar']['position'], rows['Sinov Orinbosar']['reports_to']),
                         ('Rais o‘rinbosari; nazoratidagi bo‘linmalar: Qurilish loyiha ofisi', 'Boshqaruv raisi'))
        self.assertEqual((rows['Sinov Quruvchi']['position'], rows['Sinov Quruvchi']['reports_to']),
                         ('Bo‘linma boshlig‘i', 'Sinov Orinbosar (o‘rinbosar)'))
        self.assertEqual((rows['Sinov Usta']['position'], rows['Sinov Usta']['reports_to']),
                         ('Xodim', 'Sinov Quruvchi (bo‘linma boshlig‘i)'))
        # A unit with no head and no deputy answers to the chair directly.
        self.assertEqual(rows['Sinov Yurist']['reports_to'], 'Boshqaruv raisi (bevosita)')
        self.assertIn('[Rais o‘rinbosari]', agent_replies.render('staff_overview', self.overview(self.chair)))

    def test_one_department(self):
        data = self.overview(self.chair, self.legal.pk)
        self.assertEqual({row['name'] for row in data['people']}, {'Sinov Yurist', 'Sinov Kichik'})

    def test_limited_to_whom_the_user_may_assign(self):
        self.assertEqual(self.overview(self.builder)['people'], [])          # a head with nobody under them
        with self.assertRaises(PermissionDenied):
            self.overview(self.junior)

    def test_offered_over_mcp(self):
        self.assertIn('staff_overview', {tool['name'] for tool in mcp_tools()})

    # The field itself

    def test_chair_edits_expertise_and_the_assistant_does_not_wipe_it(self):
        self.client.force_login(self.chair)
        page = self.client.get(reverse('employee_edit', args=[self.lawyer.pk]))
        self.assertContains(page, 'Malakasi va tajribasi')
        conversation = AgentConversation.objects.create(user=self.chair)
        args = tools.EmployeeChange(employee_id=self.lawyer.pk, username=None, full_name=None, job_title='Yuristkonsult',
                                    department_id=None, role=None, is_active=None)
        proposal = tools.prepare(self.chair, conversation, 'prepare_employee', args)['proposal']['id']
        tools.confirm(self.chair, conversation, proposal)
        self.lawyer.refresh_from_db()
        self.assertEqual(self.lawyer.job_title, 'Yuristkonsult')
        self.assertIn('huquqshunos', self.lawyer.expertise)

    # Loading it from a file

    def load(self, items, *args):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as handle:
            json.dump(items, handle, ensure_ascii=False)
        out = StringIO()
        call_command('import_expertise', handle.name, *args, stdout=out)
        return out.getvalue()

    def test_import_previews_then_saves_and_skips_unknown_names(self):
        items = [{'name': 'Sinov Kichik Sinovich', 'expertise': 'Mutaxassisligi: iqtisodchi.'},
                 {'name': 'Yo‘q Odam', 'expertise': 'x'}, {'name': 'Mutaxassis', 'expertise': 'lavozim, ism emas'}]
        text = self.load(items)
        self.assertIn('Sinov Kichik (kichik): kiritiladi', text)
        self.assertIn('? Yo‘q Odam: topilmadi', text)
        self.assertIn('? Mutaxassis', text)                       # a job title is not a name
        self.junior.refresh_from_db()
        self.assertEqual(self.junior.expertise, '')
        self.assertIn('Saqlandi: 1 ta', self.load(items, '--apply'))
        self.junior.refresh_from_db()
        self.assertEqual(self.junior.expertise, 'Mutaxassisligi: iqtisodchi.')
        self.assertIn('o‘zgarmaydi', self.load(items, '--apply'))

    def test_import_rejects_a_malformed_file(self):
        with self.assertRaises(CommandError):
            self.load({'name': 'x'})
        with self.assertRaises(CommandError):
            call_command('import_expertise', 'yoq-fayl.json', stdout=StringIO())
