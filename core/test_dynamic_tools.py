import json
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from . import dynamic_tools
from .agent_views import conversation_for
from .models import AgentConversation, AgentProposal, Event, Task, User
from .test_agent import AgentTestBase


def step(id, operation, **args):
    return {'id': id, 'operation': operation, 'arguments_json': json.dumps(args)}


def definition(name='dyn_department_delays', parameters=(), steps=None):
    return {'name': name, 'description': 'Bo‘linmalar bo‘yicha kechikishlar', 'parameters': list(parameters),
            'steps': steps or [
                step('tasks', 'query_tasks', status='all', assignee_id=None, query='', fields=['department', 'overdue_days']),
                step('grouped', 'group_rows', rows='$steps.tasks.rows', by=['department'], metrics=[
                    {'name': 'count', 'operation': 'count', 'field': None},
                    {'name': 'avg_delay', 'operation': 'avg', 'field': 'overdue_days'}]),
                step('sorted', 'sort_rows', rows='$steps.grouped.rows', field='count', descending=True)]}


REPORT_STEPS = [
    step('found', 'search_tasks', query='Hisobot', status='active', assignee_id=None, page=1),
    step('prepare', 'prepare_action', task_id='$steps.found.tasks.0.id', action='request_report', text='', due_at=None, no_deadline=False)]


class DynamicToolTests(AgentTestBase):
    def create_call(self, raw):
        item = self.call('create_tool')
        item['arguments'] = json.dumps(raw)
        return item

    def outputs(self, index):
        return [json.loads(item['output']) for item in json.loads(self.requests[index].content)['input']
                if item.get('type') == 'function_call_output']

    def test_created_tool_is_called_in_same_turn_and_persists_for_conversation(self):
        Task.objects.create(issuer=self.chair, assignee=self.head, title='Kechikkan', due_at=timezone.now()-timedelta(days=2))
        with self.model(self.create_call(definition()), self.call('dyn_department_delays'),
                        self.call('answer_from_source', source='r2', focus='overview')):
            data = self.message('Bo‘limlar bo‘yicha kechikishlarni hisobla').json()
        self.assertIn('Natija: 1 ta qator', data['message'])
        self.assertIn('department: Qurilish; count: 2; avg_delay: 1.00', data['message'])
        self.assertIn('dyn_department_delays', [t['name'] for t in json.loads(self.requests[1].content)['tools']])
        self.conversation.refresh_from_db()
        self.assertEqual(list(self.conversation.tools), ['dyn_department_delays'])
        with self.model(self.call('dyn_department_delays'), self.call('answer_from_source', source='r1', focus='count')):
            data = self.message('Yana hisobla').json()
        self.assertEqual(data['message'], 'Natija: 1 ta qator.')
        self.assertTrue(all(tool['strict'] for tool in json.loads(self.requests[-1].content)['tools']))

    def test_invalid_definition_is_reported_to_model_and_not_stored(self):
        for raw in [definition(steps=[step('grouped', 'group_rows', rows='$steps.missing.rows', by=[], metrics=[])]),
                    definition(steps=[step('sql', 'execute_sql', sql='DELETE FROM core_task')])]:
            with self.subTest(operation=raw['steps'][0]['operation']), self.model(
                    self.create_call(raw), self.call('ask_clarification', kind='intent')):
                data = self.message('Hisobla').json()
            self.assertIn('aniq tushunmadim', data['message'])
            self.assertIn('error', self.outputs(-1)[-1])
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.tools, {})
        self.assertTrue(Task.objects.exists())

    def test_runtime_errors_return_to_model_and_parameters_are_typed(self):
        raw = definition('dyn_person_tasks', [{'name': 'person', 'type': 'integer', 'description': 'Xodim ID', 'nullable': False}],
                         [step('tasks', 'query_tasks', status='all', assignee_id='$params.person', query='', fields=['code', 'title'])])
        dynamic_tools.create(self.head, self.conversation, raw)
        with self.model(self.call('dyn_person_tasks', person=self.other.pk), self.call('dyn_person_tasks', person='x'),
                        self.call('dyn_person_tasks', person=self.employee.pk), self.call('answer_from_source', source='r1', focus='overview')):
            data = self.message('Xalimov topshiriqlarini hisobla').json()
        self.assertNotIn(self.foreign.title, json.dumps([json.loads(r.content) for r in self.requests], ensure_ascii=False))
        self.assertIn('error', self.outputs(1)[-1])
        self.assertIn('error', self.outputs(2)[-1])
        self.assertIn(self.task.title, data['message'])

    def test_write_workflow_only_prepares_confirmation(self):
        events = Event.objects.count()
        with self.model(self.create_call(definition('dyn_request_report', [], REPORT_STEPS)), self.call('dyn_request_report')):
            data = self.message('Hisobot so‘rovini yubor').json()
        self.assertEqual(data['proposal']['preview']['Amal'], 'Hisobot so‘rash')
        self.assertEqual(Event.objects.count(), events)
        self.assertEqual(AgentProposal.objects.get().state, 'pending')

    def test_read_only_request_hides_and_rejects_write_tools(self):
        raw = definition('dyn_request_report', [], REPORT_STEPS)
        with self.assertRaises(ValidationError):
            dynamic_tools.create(self.head, self.conversation, raw, read_only=True)
        dynamic_tools.create(self.head, self.conversation, raw)
        self.assertEqual(dynamic_tools.schemas(self.head, self.conversation, read_only=True), [])
        with self.model(self.call('dyn_request_report'), self.call('ask_clarification', kind='intent')):
            self.message('Muddatsiz topshiriqlarni ko‘rsating')
        self.assertIn('error', self.outputs(-1)[-1])
        self.assertFalse(AgentProposal.objects.exists())

    def test_tool_limit_ownership_and_scope_reset(self):
        for i in range(dynamic_tools.MAX_TOOLS):
            dynamic_tools.create(self.head, self.conversation, definition(f'dyn_tool_{i}'))
        dynamic_tools.create(self.head, self.conversation, definition('dyn_tool_0'))
        with self.assertRaises(ValidationError):
            dynamic_tools.create(self.head, self.conversation, definition('dyn_tool_extra'))
        with self.assertRaises(PermissionDenied):
            dynamic_tools.run(self.employee, self.conversation, 'dyn_tool_0', {}, set())
        User.objects.filter(pk=self.head.pk).update(role='employee')
        self.assertEqual(conversation_for(User.objects.get(pk=self.head.pk), str(self.conversation.pk)).tools, {})
        self.assertEqual(AgentConversation.objects.get(pk=self.conversation.pk).tools, {})

    def test_large_results_are_truncated_before_reaching_model(self):
        Task.objects.bulk_create([Task(issuer=self.head, assignee=self.employee, title=f'Vazifa {i}') for i in range(60)])
        raw = definition('dyn_all_titles', [], [step('tasks', 'query_tasks', status='all', assignee_id=None, query='Vazifa', fields=['title'])])
        dynamic_tools.create(self.head, self.conversation, raw)
        result = dynamic_tools.run(self.head, self.conversation, 'dyn_all_titles', {}, set())
        self.assertEqual(result['data']['total'], 60)
        self.assertEqual(len(result['data']['rows']), 40)
        self.assertTrue(result['data']['truncated'])
