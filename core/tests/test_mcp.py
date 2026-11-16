import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ..mcp.server import digest, new_token
from ..models import AttendanceExcuse, Department, ExcuseRequest, McpToken, Task, User


@override_settings(TELEGRAM_BOT_TOKEN='', ATTENDANCE_EXEMPT_ROLES=['chair'], MCP_ENABLED=True)
class McpTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='Moliya')
        self.chair = User.objects.create_user('rais', full_name='Sinov Rais', role='chair')
        self.head = User.objects.create_user('boshliq', full_name='Sinov Boshliq', role='head', department=self.department)
        self.worker = User.objects.create_user('xodim', full_name='Sinov Xodim', role='employee', department=self.department)
        self.outsider = User.objects.create_user('begona', full_name='Sinov Begona', role='employee')
        self.task = Task.objects.create(issuer=self.head, assignee=self.worker, title='Hisobot tayyorlash')
        self.key = self.issue(self.chair)

    def issue(self, user, name='Sinov'):
        raw, stored = new_token()
        McpToken.objects.create(user=user, name=name, digest=stored, hint=raw[-4:])
        return raw

    def rpc(self, key, method, params=None, identifier=1, **extra):
        body = {'jsonrpc': '2.0', 'method': method, **({'id': identifier} if identifier is not None else {}),
                **({'params': params} if params is not None else {})}
        return self.client.post(reverse('mcp'), json.dumps(body), content_type='application/json',
                                HTTP_AUTHORIZATION=f'Bearer {key}', **extra)

    def tool(self, key, name, **arguments):
        result = self.rpc(key, 'tools/call', {'name': name, 'arguments': arguments}).json()['result']
        return json.loads(result['content'][0]['text']), result['isError']

    # Protocol

    def test_handshake(self):
        result = self.rpc(self.key, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                                        'clientInfo': {'name': 'test', 'version': '1'}}).json()
        self.assertEqual((result['jsonrpc'], result['id']), ('2.0', 1))
        self.assertEqual(result['result']['protocolVersion'], '2025-06-18')
        self.assertIn('tools', result['result']['capabilities'])
        self.assertIn('Sinov Rais', result['result']['instructions'])
        self.assertEqual(self.rpc(self.key, 'initialize', {'protocolVersion': '2099-01-01'}).json()['result']['protocolVersion'],
                         '2025-06-18')
        notified = self.rpc(self.key, 'notifications/initialized', identifier=None)
        self.assertEqual((notified.status_code, notified.content), (202, b''))
        self.assertEqual(self.rpc(self.key, 'ping').json()['result'], {})

    def test_tools_are_listed_with_schemas(self):
        tools = {tool['name']: tool for tool in self.rpc(self.key, 'tools/list').json()['result']['tools']}
        self.assertLessEqual({'search_tasks', 'get_task', 'list_people', 'attendance_month', 'prepare_task',
                              'prepare_excuse_request', 'confirm_action'}, set(tools))
        self.assertFalse({'navigate', 'navigate_account', 'open_attendance', 'open_report'} & set(tools))
        for tool in tools.values():
            self.assertEqual(tool['inputSchema']['type'], 'object')
            self.assertTrue(tool['description'])
        self.assertTrue(tools['search_tasks']['annotations']['readOnlyHint'])
        self.assertTrue(tools['confirm_action']['annotations']['destructiveHint'])

    def test_malformed_requests(self):
        post = lambda body: self.client.post(reverse('mcp'), body, content_type='application/json',
                                             HTTP_AUTHORIZATION=f'Bearer {self.key}')
        self.assertEqual(post('{oops').json()['error']['code'], -32700)
        self.assertEqual(post('{"id": 1, "method": "ping"}').json()['error']['code'], -32600)
        self.assertEqual(self.rpc(self.key, 'resources/list').json()['error']['code'], -32601)
        self.assertEqual(self.rpc(self.key, 'tools/call', {'name': 'navigate', 'arguments': {}}).json()['error']['code'], -32602)
        self.assertEqual(self.rpc(self.key, 'tools/call', {'name': 'drop_everything', 'arguments': {}}).json()['error']['code'], -32602)
        batch = post(json.dumps([{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
                                 {'jsonrpc': '2.0', 'method': 'notifications/initialized'}])).json()
        self.assertEqual(batch, [{'jsonrpc': '2.0', 'id': 1, 'result': {}}])
        self.assertEqual(self.client.get(reverse('mcp'), HTTP_AUTHORIZATION=f'Bearer {self.key}').status_code, 405)

    # Who is speaking

    def test_no_token_no_entry(self):
        url = reverse('mcp')
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        for headers in [{}, {'HTTP_AUTHORIZATION': 'Bearer tn_wrong'}, {'HTTP_AUTHORIZATION': 'Basic abc'}]:
            response = self.client.post(url, body, content_type='application/json', **headers)
            self.assertEqual(response.status_code, 401)
            self.assertIn('Bearer', response['WWW-Authenticate'])
        self.client.force_login(self.head)                        # a browser session is not a key
        self.assertEqual(self.client.post(url, body, content_type='application/json').status_code, 401)

    def test_key_in_the_address_works_like_the_header(self):
        response = self.client.post(reverse('mcp_token', args=[self.key]),
                                    json.dumps({'jsonrpc': '2.0', 'id': 7, 'method': 'ping'}), content_type='application/json')
        self.assertEqual(response.json(), {'jsonrpc': '2.0', 'id': 7, 'result': {}})
        self.assertEqual(self.client.post(reverse('mcp_token', args=['tn_wrong']), '{}', content_type='application/json').status_code, 401)

    def test_revoked_key_and_blocked_employee_are_refused(self):
        second = self.issue(self.chair)
        McpToken.objects.filter(digest=digest(second)).update(revoked_at=timezone.now())
        self.assertEqual(self.rpc(second, 'ping').status_code, 401)
        self.assertEqual(self.rpc(self.key, 'ping').status_code, 200)
        User.objects.filter(pk=self.chair.pk).update(is_active=False)
        self.assertEqual(self.rpc(self.key, 'ping').status_code, 401)

    def test_only_the_chair_connects(self):
        secretary = User.objects.create_user('kotib', full_name='Sinov Kotib', role='secretary')
        deputy = User.objects.create_user('orinbosar', full_name='Sinov Orinbosar', role='deputy')
        for person in (self.worker, self.head, secretary, deputy):
            key = self.issue(person)                              # even with a row in the table
            self.assertEqual(self.rpc(key, 'tools/list').status_code, 401)
            self.assertEqual(self.client.post(reverse('mcp_token', args=[key]), '{}',
                                              content_type='application/json').status_code, 401)
        # The key stops with the role, and comes back with it.
        User.objects.filter(pk=self.chair.pk).update(role='deputy')
        self.assertEqual(self.rpc(self.key, 'ping').status_code, 401)
        User.objects.filter(pk=self.chair.pk).update(role='chair')
        self.assertEqual(self.rpc(self.key, 'ping').status_code, 200)

    def test_last_use_is_recorded_but_only_the_digest_is_stored(self):
        self.rpc(self.key, 'ping')
        token = McpToken.objects.get(user=self.chair)
        self.assertIsNotNone(token.last_used_at)
        self.assertNotIn(self.key, json.dumps(list(McpToken.objects.values('digest', 'hint', 'name'))))
        self.assertEqual(len(token.digest), 64)

    @override_settings(MCP_ENABLED=False)
    def test_switch_closes_the_endpoint_and_the_page(self):
        self.assertEqual(self.rpc(self.key, 'ping').status_code, 404)
        self.client.force_login(self.chair)
        self.assertEqual(self.client.get(reverse('claude_connect')).status_code, 404)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'Claude’ni ulash')

    # Acting as that employee

    def test_chair_reads_tasks_and_attendance(self):
        data, failed = self.tool(self.key, 'search_tasks', query='', status='all', assignee_id=None, page=1)
        self.assertFalse(failed)
        self.assertEqual([task['title'] for task in data['tasks']], ['Hisobot tayyorlash'])
        self.assertEqual(self.tool(self.key, 'get_task', task_id=self.task.pk)[0]['assignee'], 'Sinov Xodim')
        data, failed = self.tool(self.key, 'attendance_day', date=None, only='all')
        self.assertFalse(failed)
        self.assertNotIn('Sinov Rais', json.dumps(data, ensure_ascii=False))     # the chair is exempt from attendance
        self.assertEqual(self.tool(self.key, 'get_task', task_id=999999),
                         ({'error': 'Topshiriq topilmadi yoki uni ko‘rish huquqingiz yo‘q.'}, True))

    def test_bad_arguments_come_back_as_a_tool_error(self):
        data, failed = self.tool(self.key, 'get_task', task_id='T-101')
        self.assertTrue(failed)
        self.assertIn('task_id', data['error'])
        self.assertTrue(self.tool(self.key, 'confirm_action', proposal_id='not-a-uuid', cancel=False)[1])

    def test_change_is_previewed_then_confirmed(self):
        due = (timezone.localtime() + timedelta(days=3)).replace(microsecond=0).isoformat()
        data, failed = self.tool(self.key, 'prepare_task', title='Shartnoma loyihasi', description='', assignee_id=self.worker.pk,
                                 due_at=due, no_deadline=False, parent_id=None, letter_number=None, letter_date=None,
                                 letter_sender=None)
        self.assertFalse(failed)
        self.assertEqual(data['proposal']['preview']['Ijrochi'], 'Sinov Xodim')
        self.assertIn('confirm_action', data['next'])
        self.assertEqual(Task.objects.count(), 1)                 # nothing saved yet
        done, failed = self.tool(self.key, 'confirm_action', proposal_id=data['proposal']['id'], cancel=False)
        self.assertFalse(failed)
        self.assertEqual(Task.objects.filter(title='Shartnoma loyihasi', issuer=self.chair, assignee=self.worker).count(), 1)
        self.assertNotIn('navigation', done)

    def test_cancelled_preview_saves_nothing(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        data, _ = self.tool(self.key, 'prepare_excuse_entry', employee_id=self.worker.pk, kind='trip', date=tomorrow,
                            until=None, expected_at=None, note='Toshkent')
        self.assertEqual(data['proposal']['preview']['Xodim'], 'Sinov Xodim')
        self.assertEqual(self.tool(self.key, 'confirm_action', proposal_id=data['proposal']['id'], cancel=True),
                         ({'message': 'Amal bekor qilindi.', 'proposal': None}, False))
        self.assertTrue(self.tool(self.key, 'confirm_action', proposal_id=data['proposal']['id'], cancel=False)[1])
        self.assertFalse(ExcuseRequest.objects.exists())
        self.assertFalse(AttendanceExcuse.objects.exists())

    # The page that issues keys

    def test_chair_opens_and_revokes_a_connection(self):
        McpToken.objects.all().delete()
        self.client.force_login(self.chair)
        self.assertContains(self.client.get(reverse('dashboard')), 'Claude’ni ulash')
        page = self.client.post(reverse('claude_connect'), {'name': 'Ish kompyuterim'})
        key = page.context['secret']
        self.assertTrue(key.startswith('tn_'))
        self.assertContains(page, key)
        self.assertIn('no-store', page['Cache-Control'])
        token = McpToken.objects.get(user=self.chair)
        self.assertEqual((token.name, token.hint, token.digest), ('Ish kompyuterim', key[-4:], digest(key)))
        self.assertEqual(self.rpc(key, 'ping').status_code, 200)
        again = self.client.get(reverse('claude_connect'))
        self.assertNotContains(again, key)                        # shown once
        self.assertContains(again, 'Ish kompyuterim')
        # Nobody revokes someone else's connection.
        other = McpToken.objects.create(user=self.worker, name='boshqa', digest='0' * 64, hint='0000')
        self.client.post(reverse('claude_connect'), {'revoke': other.pk})
        other.refresh_from_db()
        self.assertIsNone(other.revoked_at)
        self.client.post(reverse('claude_connect'), {'revoke': token.pk})
        self.assertEqual(self.rpc(key, 'ping').status_code, 401)

    def test_connections_are_limited(self):
        self.client.force_login(self.chair)
        for _ in range(6):
            self.client.post(reverse('claude_connect'), {'name': 'x'})
        self.assertEqual(McpToken.objects.filter(user=self.chair, revoked_at__isnull=True).count(), 5)

    def test_nobody_else_has_the_page_or_the_link(self):
        for person in (self.head, self.worker, self.outsider):
            self.client.force_login(person)
            self.assertNotContains(self.client.get(reverse('dashboard')), 'Claude’ni ulash')
            self.assertEqual(self.client.get(reverse('claude_connect')).status_code, 404)
            self.assertEqual(self.client.post(reverse('claude_connect'), {'name': 'x'}).status_code, 404)
        self.assertFalse(McpToken.objects.exclude(user=self.chair).exists())

    def test_page_needs_a_login(self):
        self.assertEqual(self.client.get(reverse('claude_connect')).status_code, 302)
