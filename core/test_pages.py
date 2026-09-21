import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from . import dynamic_tools, pages
from .agent_views import conversation_for
from .models import Department, GeneratedPage, Task, User
from .test_agent import AgentTestBase
from .test_dynamic_tools import definition, step

LAYOUT = '<h1>{{title}}</h1><p class="lead">Holat</p>{{chart}}{{table}}<footer>{{total}} qator · {{generated_at}}</footer>'
BY_PERSON = [step('tasks', 'query_tasks', status='all', assignee_id=None, query='', fields=['assignee']),
             step('grouped', 'group_rows', rows='$steps.tasks.rows', by=['assignee'],
                  metrics=[{'name': 'count', 'operation': 'count', 'field': None}])]


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class GeneratedPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        dep = Department.objects.create(name='Qurilish')
        cls.head = User.objects.create_user('boshliq', full_name='Tuyliyev Asliddin', role='head', department=dep)
        cls.employee = User.objects.create_user('xodim', full_name='Xalimov Farrux', department=dep)
        cls.other = User.objects.create_user('other', full_name='Maxfiy Xodim')
        Task.objects.create(issuer=cls.head, assignee=cls.employee, title='Hisobot')

    def setUp(self):
        self.conversation = conversation_for(self.head, '11111111-1111-4111-8111-111111111111')
        dynamic_tools.create(self.head, self.conversation, definition('dyn_by_person', [], BY_PERSON))
        self.client.force_login(self.head)

    def build(self, **extra):
        raw = {'title': 'Ijro holati', 'html': LAYOUT, 'tool': 'dyn_by_person', 'arguments_json': '{}', **extra}
        return pages.execute(self.head, self.conversation, 'create_page', raw)

    def test_page_renders_server_data_in_the_model_layout(self):
        result = self.build()
        page = GeneratedPage.objects.get()
        self.assertEqual(result['navigation']['url'], f'/pages/{page.pk}/')
        body = pages.body(self.head, page)
        self.assertIn('<h1>Ijro holati</h1>', body)
        self.assertIn('<p class="lead">Holat</p>', body)
        self.assertIn(self.employee.full_name, body)
        self.assertIn('<table class="page-table">', body)
        self.assertIn('<svg class="page-chart"', body)
        self.assertIn('1 qator', body)
        response = self.client.get(f'/pages/{page.pk}/content/')
        self.assertContains(response, self.employee.full_name)
        self.assertIn('sandbox', response['Content-Security-Policy'])
        self.assertContains(self.client.get(f'/pages/{page.pk}/'), f'/pages/{page.pk}/content/')
        self.assertContains(self.client.get('/pages/'), 'Ijro holati')

    def test_scripts_forms_and_external_content_are_removed(self):
        html = ('<h1>Salom</h1><script>fetch("//evil/?x=1")</script><img src="//evil/pixel.png">'
                '<a href="//evil">havola</a><iframe src="//evil"></iframe><form action="/tasks/"><input name="x"></form>'
                '<div onclick="alert(1)" class="ok" style="color:red;background:url(//evil/x.png)">matn</div>'
                '<style>.a{background:url(//evil/y.png)}</style>{{table}}')
        clean = pages.sanitize(html)
        for fragment in ['<script', 'evil', 'onclick', '<img', '<iframe', '<form', '<input', 'url(']:
            self.assertNotIn(fragment, clean)
        for fragment in ['<h1>Salom</h1>', 'class="ok"', 'havola', 'matn', '{{table}}']:
            self.assertIn(fragment, clean)

    def test_layout_without_data_placeholder_or_write_tool_is_refused(self):
        dynamic_tools.create(self.head, self.conversation, definition('dyn_writer', [], [
            step('found', 'search_tasks', query='', status='active', assignee_id=None, page=1),
            step('prepare', 'prepare_action', task_id='$steps.found.tasks.0.id', action='request_report',
                 text='', due_at=None, no_deadline=False)]))
        for extra in [{'html': '<h1>Hammasi yaxshi</h1><p>289 ta topshiriq bajarildi</p>'},
                      {'tool': 'dyn_missing'}, {'tool': 'dyn_writer'}, {'title': 'ab'},
                      {'arguments_json': '[]'}, {'html': 'x' * (pages.MAX_HTML + 1)}]:
            with self.subTest(extra=list(extra)), self.assertRaises(ValidationError):
                self.build(**extra)
        self.assertFalse(GeneratedPage.objects.exists())

    def test_pages_are_private_and_data_follows_the_viewer(self):
        self.build()
        page = GeneratedPage.objects.get()
        self.client.force_login(self.employee)
        for url in [f'/pages/{page.pk}/', f'/pages/{page.pk}/content/']:
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(f'/pages/{page.pk}/delete/').status_code, 404)
        self.assertNotContains(self.client.get('/pages/'), 'Ijro holati')
        self.assertNotIn(self.employee.full_name, pages.body(self.other, page))
        self.assertEqual(pages.execute(self.other, self.conversation, 'list_pages', {})['pages'], [])
        with self.assertRaises(PermissionDenied):
            pages.execute(self.other, self.conversation, 'create_page',
                          {'title': 'O‘zga suhbat', 'html': LAYOUT, 'tool': 'dyn_by_person', 'arguments_json': '{}'})

    def test_same_title_replaces_and_limit_applies(self):
        self.build()
        self.build(html='<h2>Yangilandi</h2>{{total}}')
        self.assertEqual(GeneratedPage.objects.count(), 1)
        self.assertIn('Yangilandi', GeneratedPage.objects.get().html)
        for index in range(pages.MAX_PAGES - 1):
            self.build(title=f'Sahifa {index}')
        with self.assertRaises(ValidationError):
            self.build(title='Ortiqcha sahifa')
        self.assertEqual(len(pages.execute(self.head, self.conversation, 'list_pages', {})['pages']), pages.MAX_PAGES)
        page = GeneratedPage.objects.first()
        self.assertEqual(pages.execute(self.head, self.conversation, 'open_page', {'page_id': page.pk})['navigation']['url'],
                         page.get_absolute_url())
        with self.assertRaises(ValidationError):
            pages.execute(self.head, self.conversation, 'open_page', {'page_id': 999999})
        self.client.post(f'/pages/{page.pk}/delete/')
        self.assertFalse(GeneratedPage.objects.filter(pk=page.pk).exists())


class AgentPageTests(AgentTestBase):
    def test_agent_builds_a_page_and_navigates_to_it(self):
        create_tool = self.call('create_tool')
        create_tool['arguments'] = json.dumps(definition('dyn_by_person', [], BY_PERSON))
        create_page = self.call('create_page', title='Ijro holati', html=LAYOUT,
                                tool='dyn_by_person', arguments_json='{}')
        with self.model(create_tool, create_page):
            data = self.message('Ijro holati bo‘yicha sahifa tayyorla').json()
        page = GeneratedPage.objects.get(user=self.head)
        self.assertEqual(data['navigation']['url'], f'/pages/{page.pk}/')
        self.assertIn(page.title, data['message'])
        self.assertIn('create_page', [tool['name'] for tool in json.loads(self.requests[0].content)['tools']])
        body = pages.body(self.head, page)
        self.assertIn(self.employee.full_name, body)

    def test_unusable_layout_is_reported_to_the_model_not_the_user(self):
        create_page = self.call('create_page', title='Soxta', html='<p>289 ta topshiriq</p>',
                                tool='dyn_missing', arguments_json='{}')
        with self.model(create_page, self.call('ask_clarification', kind='intent')):
            data = self.message('Sahifa tayyorla').json()
        self.assertIn('aniq tushunmadim', data['message'])
        self.assertFalse(GeneratedPage.objects.exists())
        outputs = [item for item in json.loads(self.requests[-1].content)['input'] if item.get('type') == 'function_call_output']
        self.assertIn('error', json.loads(outputs[-1]['output']))

    def test_saved_page_is_opened_only_on_a_navigation_request(self):
        page = GeneratedPage.objects.create(user=self.head, title='Ijro holati', html='{{total}}',
            tool=dynamic_tools.parse_definition(definition('dyn_by_person', [], BY_PERSON)).model_dump(), arguments={})
        with self.model(self.call('open_page', page_id=page.pk)):
            data = self.message('Ijro holati sahifasini och').json()
        self.assertEqual(data['navigation']['url'], page.get_absolute_url())
        with self.model(self.call('open_page', page_id=page.pk)):
            data = self.message('Menga umumiy holat kerak').json()
        self.assertNotIn('navigation', data)
