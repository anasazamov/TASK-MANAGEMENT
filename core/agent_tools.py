"""Domain tools: only named application routes and existing workflow services."""
from datetime import datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from .forms import TaskForm
from .models import AgentProposal, Event, Task, User
from .permissions import assignees_for, can_delegate, can_manage
from .person_search import find_people
from .person_search import words, variants
from . import navigation_intent
from .services import create_task, require, task_action

Status = Literal['active', 'all', 'overdue', 'soon', 'submitted', 'accepted', 'undated']
Page = Literal['dashboard', 'tasks', 'task_detail', 'task_create', 'employees', 'structure', 'chains', 'timeline', 'notifications']
Action = Literal['comment', 'submit', 'accept', 'return', 'set_deadline', 'request_deadline', 'approve_deadline', 'reject_deadline', 'request_report', 'report']
ID = Annotated[int, Field(ge=1, le=2147483647)]


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Search(Arguments):
    query: str = Field(max_length=200)
    status: Status
    assignee_id: ID | None
    page: int = Field(ge=1, le=100)


class TaskID(Arguments):
    task_id: ID


class People(Arguments):
    query: str = Field(max_length=180)


class Navigate(Arguments):
    page: Page
    task_id: ID | None
    status: Status
    query: str = Field(max_length=200)
    assignee_id: ID | None


class NewTask(Arguments):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(max_length=6000)
    assignee_id: ID
    due_at: str | None
    no_deadline: bool
    parent_id: ID | None


class ChangeTask(Arguments):
    task_id: ID
    action: Action
    text: str = Field(max_length=6000)
    due_at: str | None
    no_deadline: bool


class Activity(Arguments):
    days: int = Field(ge=1, le=90)


class EmployeeChange(Arguments):
    employee_id: ID | None
    username: str | None = Field(max_length=150)
    full_name: str | None = Field(max_length=180)
    job_title: str | None = Field(max_length=180)
    department_id: ID | None
    role: Literal['employee', 'head'] | None
    is_active: bool | None


class DepartmentChange(Arguments):
    department_id: ID | None
    name: str = Field(min_length=1, max_length=180)


class TaskEdit(Arguments):
    task_id: ID
    title: str | None = Field(min_length=1, max_length=240)
    description: str | None = Field(max_length=6000)
    assignee_id: ID | None


class AccountPage(Arguments):
    page: Literal['password_change', 'employee_create', 'employee_edit', 'employee_password']
    employee_id: ID | None


TOOLS = {
    'read_structure': (Arguments, 'Read authorized departments and employee accounts, including inactive accounts, roles, and department IDs. Required before employee/department changes.'),
    'prepare_employee': (EmployeeChange, 'Prepare employee creation/update/block/unblock. Chair only. employee_id=null creates; creation requires username/full_name/department_id/role. Null fields on update stay unchanged. No passwords: created accounts require setting a password on the secure page.'),
    'prepare_department': (DepartmentChange, 'Prepare creating a department (null ID) or renaming a real department. Chair only.'),
    'prepare_task_edit': (TaskEdit, 'Prepare task title, description or assignee changes. Null fields stay unchanged. Manager only; closed tasks cannot change. Use prepare_action for deadlines/status.'),
    'prepare_notifications_read': (Arguments, 'Prepare marking currently unread authorized notifications as read.'),
    'navigate_account': (AccountPage, 'Open own password change or chair-only employee create/edit/password form. Passwords must be entered on the secure form, never in voice/chat.'),
    'search_tasks': (Search, 'Search visible tasks by title/content, status and assignee. Returns real IDs and codes; paginate with page.'),
    'get_task': (TaskID, 'Read one visible task, history, children, deadline request and permitted actions. task_id is the database ID, not the T-code.'),
    'get_summary': (Arguments, 'Get current counts and overdue tasks within the user role.'),
    'list_people': (People, 'Find visible employees by first name, surname, full name or job/department. Pass only the person name, not the whole command. Handles Uzbek case suffixes, apostrophes and Cyrillic. If needs_clarification is true, ask the user to select/confirm before using an ID. can_assign controls assignment eligibility.'),
    'navigate': (Navigate, 'Open an application page. For lists or status categories use tasks, even with one matching task. Only for one explicitly identified task use task_detail and its real ID. Use null for no assignee filter. Never navigates to external URLs.'),
    'prepare_task': (NewTask, 'Prepare a new or delegated task for user confirmation. Does not save. Ask for missing deadline or explicit no-deadline first.'),
    'prepare_action': (ChangeTask, 'Prepare an existing task action for confirmation. Does not execute it. Read the task first and use permitted_actions.'),
    'get_notifications': (Arguments, 'Read the user’s latest visible notifications.'),
    'recent_activity': (Activity, 'Read the latest visible task history for the specified number of days.'),
}


def schemas():
    return [{'type': 'function', 'name': name, 'description': description, 'strict': True,
             'parameters': model.model_json_schema()} for name, (model, description) in TOOLS.items()]


def visible(user):
    return Task.objects.visible_to(user).enriched()


def get_task(user, pk):
    task = visible(user).filter(pk=pk).first()
    if not task:
        raise ValidationError('Topshiriq topilmadi yoki uni ko‘rish huquqingiz yo‘q.')
    return task


def people(user):
    if user.is_chair:
        return User.objects.filter(is_active=True)
    if user.can_assign:
        return User.objects.filter(Q(department_id=user.department_id) | Q(pk=user.pk), is_active=True) if user.department_id else User.objects.filter(pk=user.pk)
    return User.objects.filter(pk=user.pk)


def check_person(user, pk):
    if pk is not None and not people(user).filter(pk=pk).exists():
        raise PermissionDenied('Bu xodimni ko‘rish huquqingiz yo‘q.')


def actions_for(user, task):
    actions = ['comment']
    if task.status == 'accepted':
        return actions
    if task.assignee_id == user.pk:
        actions.append('report')
        if task.status == 'active':
            actions.extend(['submit', 'request_deadline'])
    if can_manage(user, task):
        actions.append('request_report')
        if task.status == 'active':
            actions.append('set_deadline')
        if task.status == 'submitted':
            actions.extend(['accept', 'return'])
        if task.deadline_requests.filter(state='pending').exists():
            actions.extend(['approve_deadline', 'reject_deadline'])
    return actions


def dashboard_decisions(user):
    """Same role and state scope as the dashboard's decision cards."""
    tasks = visible(user).exclude(assignee_id=user.pk)
    if not user.is_chair:
        tasks = tasks.filter(issuer_id=user.pk)
    result = []
    for kind, query in [('deadline_request', tasks.filter(deadline_requests__state='pending').distinct()),
                        ('submitted', tasks.filter(status='submitted'))]:
        result.extend({'id': task.pk, 'code': task.code, 'title': task.title, 'kind': kind}
                      for task in query.order_by('pk')[:40])
    return result


def title_matches(tokens, title):
    remaining = words(title)
    for token in tokens:
        index = next((i for i, part in enumerate(remaining) if variants(token) & variants(part)), None)
        if index is None:
            return False
        remaining.pop(index)
    return bool(tokens)


def current_list_shortcut(user, command, context, history):
    if 'current_list_tasks' not in context or navigation_intent.negative(command) or navigation_intent.writes(command):
        return None
    tokens = words(command)
    last = history[-1] if history else {}
    previous = last.get('task_choices', []) if last.get('verified') and last.get('decision_kind') == 'current_list' and last.get('navigation_pending') else []
    fillers = navigation_intent.FILLER_WORDS | {'joriy', 'sahifadagi', 'sahifada', 'yerdagi', 'turgan', 'korinib', 'korinayotgan', 'topshiriqni', 'topshiriq', 'vazifani'}
    explicit = (any(t in ('sahifadagi', 'sahifada', 'yerdagi') for t in tokens)
                and any(t in ('topshiriqni', 'topshiriq', 'vazifani') for t in tokens)
                and navigation_intent.navigation(command)
                and all(t in fillers or navigation_intent.open_word(t) for t in tokens))
    if not explicit and not previous:
        return None
    choices = context['current_list_tasks']
    if not explicit:
        choices = [item for item in choices if item['id'] in {p['id'] for p in previous}]
        raw = command.strip().rstrip('.!?')
        if raw.isascii() and raw.isdigit() and 1 <= int(raw) <= len(previous):
            choices = [item for item in choices if item['id'] == previous[int(raw)-1]['id']]
        else:
            query = [t for t in tokens if t not in navigation_intent.FILLER_WORDS and not navigation_intent.open_word(t)]
            choices = [item for item in choices if title_matches(query, item['title'])]
            if not choices:
                return None
    if not choices:
        return {'message': 'Joriy ro‘yxatda ochiladigan topshiriq yo‘q.', 'mode': 'shortcut'}
    if len(choices) == 1:
        return {**navigate(user, Navigate(page='task_detail', task_id=choices[0]['id'], status='all', query='', assignee_id=None)), 'mode': 'shortcut'}
    choices = choices[:40]
    rows = '\n'.join(f"{i}. {item['code']} — {item['title']}" for i, item in enumerate(choices, 1))
    return {'message': 'Joriy sahifada bir nechta topshiriq bor:\n'+rows+'\nQaysi biri kerak?',
            'task_choices': choices, 'decision_kind': 'current_list', 'navigation_pending': True, 'mode': 'shortcut'}


def decision_shortcut(user, command, context, history):
    """Open a named card, or a verified choice, without generating a workflow action."""
    if 'dashboard_decisions' not in context or navigation_intent.negative(command) or navigation_intent.writes(command):
        return None
    tokens = words(command)
    kind = 'deadline_request' if any(t.startswith('muddat') for t in tokens) and any(t.startswith('sorov') for t in tokens) else None
    if 'ijro' in tokens and any(t.startswith('qabuli') for t in tokens):
        kind = 'submitted'
    last = history[-1] if history else {}
    previous = last.get('task_choices', []) if last.get('verified') and last.get('navigation_pending') else []
    explicit_kind = kind is not None
    if kind is None and previous:
        kind = last.get('decision_kind')
    if kind not in ('deadline_request', 'submitted'):
        return None
    choices = [item for item in context['dashboard_decisions'] if item['kind'] == kind]
    if previous and not explicit_kind:
        choices = [item for item in choices if item['id'] in {p['id'] for p in previous}]
    labels = {'deadline_request': 'Muddat so‘rovi', 'submitted': 'Ijro qabuli'}
    if not choices:
        return {'message': labels[kind]+' bo‘yicha bu panelda ko‘rib chiqiladigan topshiriq yo‘q.', 'mode': 'shortcut'}
    query = [t for t in tokens if not navigation_intent.open_word(t) and t not in navigation_intent.FILLER_WORDS
             and t not in ('muddat', 'muddati', 'ijro', 'boyicha') and not t.startswith(('sorov', 'qabuli', 'sahifa'))]
    selected = []
    raw = command.strip().rstrip('.!?')
    if previous and not explicit_kind and raw.isascii() and raw.isdigit() and 1 <= int(raw) <= len(previous):
        selected = [item for item in choices if item['id'] == previous[int(raw)-1]['id']]
    elif not query and explicit_kind:
        selected = choices
    elif query:
        selected = [item for item in choices if title_matches(query, item['title'])]
    if len(selected) == 1:
        result = navigate(user, Navigate(page='task_detail', task_id=selected[0]['id'], status='all', query='', assignee_id=None))
        return {**result, 'mode': 'shortcut'}
    if not selected and not explicit_kind:
        return None
    choices = selected or choices
    rows = '\n'.join(f"{i}. {item['code']} — {item['title']}" for i, item in enumerate(choices, 1))
    prefix = labels[kind]+' bo‘yicha quyidagi topshiriqlar bor:' if selected or not query else 'Bu nomga mos kartochka topilmadi. Panelda quyidagilar bor:'
    return {'message': prefix+'\n'+rows+'\nQaysi biri kerak?', 'mode': 'shortcut',
            'task_choices': choices, 'decision_kind': kind, 'navigation_pending': True}


def row(task):
    return dict(id=task.pk, code=task.code, title=task.title, status=task.state,
                assignee=task.assignee.full_name, assignee_id=task.assignee_id,
                due_at=timezone.localtime(task.due_at).isoformat() if task.due_at else None,
                url=task.get_absolute_url())


def filtered(user, query='', status='all', assignee_id=None):
    check_person(user, assignee_id)
    tasks = visible(user)
    if query:
        tasks = tasks.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(assignee__full_name__icontains=query))
    if assignee_id is not None:
        tasks = tasks.filter(assignee_id=assignee_id)
    now = timezone.now()
    if status == 'active':
        tasks = tasks.exclude(status='accepted')
    elif status == 'overdue':
        tasks = tasks.filter(status='active', due_at__lt=now)
    elif status == 'soon':
        tasks = tasks.filter(status='active', due_at__gte=now, due_at__lte=now + timedelta(days=3))
    elif status == 'undated':
        tasks = tasks.filter(status='active', due_at__isnull=True)
    elif status in ('submitted', 'accepted'):
        tasks = tasks.filter(status=status)
    return tasks


def navigate(user, args):
    if args.page == 'structure':
        require(user.is_chair)
    if args.page in ('employees', 'chains', 'task_create'):
        require(user.can_assign)
    if args.page == 'task_detail':
        task = get_task(user, args.task_id)
        url, label = task.get_absolute_url(), task.code + ' — ' + task.title
    else:
        url = reverse(args.page)
        labels = dict(dashboard='Boshqaruv paneli', tasks='Topshiriqlar', task_create='Yangi topshiriq',
                      employees='Xodimlar', structure='Struktura', chains='Nazorat zanjiri',
                      timeline='Harakatlar tarixi', notifications='Xabarnomalar')
        label = labels[args.page]
        if args.page == 'tasks':
            check_person(user, args.assignee_id)
            label = {'active': 'Faol topshiriqlar', 'all': 'Barcha topshiriqlar',
                     'overdue': 'Muddati o‘tgan topshiriqlar', 'soon': 'Muddati yaqin topshiriqlar',
                     'submitted': 'Tasdiq kutilayotgan topshiriqlar', 'accepted': 'Qabul qilingan topshiriqlar',
                     'undated': 'Muddatsiz topshiriqlar'}[args.status]
            params = {'filter': args.status}
            if args.query:
                params['q'] = args.query
            if args.assignee_id is not None:
                params['employee'] = args.assignee_id
                person = people(user).get(pk=args.assignee_id)
                label = person.full_name + ' — ' + label.lower()
            url += '?' + urlencode(params)
        if args.page == 'task_create' and args.task_id is not None:
            require(can_delegate(user, get_task(user, args.task_id)))
            url += '?' + urlencode({'parent': args.task_id})
    return {'navigation': {'url': url, 'label': label}, 'message': label + ' sahifasini ochyapman.'}


def parse_due(value):
    if value is None:
        return None
    try:
        date = datetime.fromisoformat(value)
        if timezone.is_naive(date):
            raise ValueError
        return date
    except (ValueError, TypeError):
        raise ValidationError('Muddatni sana, vaqt va Toshkent vaqt zonasi bilan aniqlashtiring.')


def due_label(due):
    return timezone.localtime(due).strftime('%d.%m.%Y %H:%M') if due else 'Muddatsiz'


def new_task_data(user, args):
    require(user.can_assign)
    parent = get_task(user, args.parent_id) if args.parent_id is not None else None
    if parent:
        require(can_delegate(user, parent))
    if args.due_at is None and not args.no_deadline:
        raise ValidationError('Muddatni so‘rang yoki foydalanuvchi «muddatsiz» deb aniqlashtirsin.')
    if args.due_at is not None and args.no_deadline:
        raise ValidationError('Sana va «muddatsiz» bir vaqtda berilmasligi kerak.')
    due = parse_due(args.due_at)
    form = TaskForm(dict(title=args.title, description=args.description, assignee=args.assignee_id,
                         due_at=due.isoformat() if due else ''), user=user, parent=parent)
    if not form.is_valid():
        raise ValidationError([str(error) for errors in form.errors.values() for error in errors])
    return {k: form.cleaned_data[k] for k in ['title', 'description', 'assignee', 'due_at']}, parent


ACTION_LABELS = dict(comment='Izoh qo‘shish', submit='Ijroni topshirish', accept='Ijroni qabul qilish',
    return_='Qayta ishlashga qaytarish', set_deadline='Muddatni o‘zgartirish', request_deadline='Muddat uzaytirishni so‘rash',
    approve_deadline='Muddat so‘rovini tasdiqlash', reject_deadline='Muddat so‘rovini rad etish',
    request_report='Hisobot so‘rash', report='Hisobot yuborish')
ACTION_LABELS['return'] = ACTION_LABELS.pop('return_')


def change_data(user, args):
    task = get_task(user, args.task_id)
    require(args.action in actions_for(user, task))
    if args.action == 'set_deadline' and args.due_at is None and not args.no_deadline:
        raise ValidationError('Yangi muddatni aniqlashtiring. Muddatsiz qilish uchun buni aniq ayting.')
    if args.due_at is not None and args.no_deadline:
        raise ValidationError('Sana va «muddatsiz» birga kelmasligi kerak.')
    due = parse_due(args.due_at)
    # Existing services have only transactional DB effects (events/notifications).
    # Dry-run in a rollback savepoint keeps preview and commit validation identical.
    with transaction.atomic():
        task_action(user, task.pk, args.action, args.text, due)
        transaction.set_rollback(True)
    return task, due


def snapshot(task):
    pending = task.deadline_requests.filter(state='pending').values_list('pk', flat=True).first()
    return f'{task.updated_at.isoformat()}:{pending or 0}'


def proposal_data(proposal):
    return {'id': str(proposal.pk), 'preview': proposal.preview, 'expires_at': proposal.expires_at.isoformat()}


def prepare(user, conversation, name, args):
    if name in ('prepare_employee', 'prepare_department', 'prepare_task_edit', 'prepare_notifications_read'):
        from . import agent_admin
        return agent_admin.prepare(user, conversation, name, args)
    if name == 'prepare_task':
        data, parent = new_task_data(user, args)
        preview = {'Amal': 'Quyi topshiriq yaratish' if parent else 'Topshiriq yaratish', 'Mazmun': data['title'],
                   'Ijrochi': data['assignee'].full_name, 'Muddat': due_label(data['due_at']), 'Talablar': data['description']}
        if parent:
            preview['Asosiy topshiriq'] = parent.code + ' — ' + parent.title
        version = snapshot(parent) if parent else ''
    else:
        task, due = change_data(user, args)
        preview = {'Amal': ACTION_LABELS[args.action], 'Topshiriq': task.code + ' — ' + task.title, 'Matn': args.text}
        if args.action in ('set_deadline', 'request_deadline'):
            preview['Yangi muddat'] = due_label(due)
        if args.action == 'approve_deadline':
            preview['Yangi muddat'] = due_label(task.deadline_requests.get(state='pending').proposed_due_at)
        version = snapshot(task)
    AgentProposal.objects.filter(conversation=conversation, state='pending').update(state='cancelled')
    proposal = AgentProposal.objects.create(conversation=conversation, payload={'tool': name, 'args': args.model_dump()},
                  preview=preview, snapshot=version, expires_at=timezone.now()+timedelta(minutes=10))
    return {'proposal': proposal_data(proposal), 'message': 'Amal tayyor. Ma’lumotlarni tekshirib, «tasdiqlayman» deng yoki tasdiqlash tugmasini bosing.'}


@transaction.atomic
def confirm(user, conversation, proposal_id, cancel=False):
    proposal = AgentProposal.objects.select_for_update().filter(pk=proposal_id, conversation=conversation, conversation__user=user).first()
    if not proposal:
        raise ValidationError('Tasdiqlanadigan amal topilmadi.')
    if proposal.state == 'completed':
        if proposal.result.get('task_id'):
            get_task(user, proposal.result['task_id'])
        elif proposal.payload['tool'] in ('prepare_employee', 'prepare_department'):
            require(user.is_active and user.is_chair)
        return proposal.result
    if proposal.state != 'pending' or proposal.expires_at <= timezone.now():
        raise ValidationError('Bu amal eskirgan yoki bekor qilingan. Buyruqni qayta bering.')
    if cancel:
        proposal.state = 'cancelled'
        proposal.save(update_fields=['state'])
        return {'message': 'Amal bekor qilindi.', 'proposal': None}
    require(user.is_active)
    name = proposal.payload['tool']
    args = TOOLS[name][0].model_validate(proposal.payload['args'])
    if name in ('prepare_employee', 'prepare_department', 'prepare_task_edit', 'prepare_notifications_read'):
        from . import agent_admin
        return agent_admin.confirm(user, proposal, name, args)
    lock_id = getattr(args, 'task_id', None) or getattr(args, 'parent_id', None)
    if lock_id:
        Task.objects.select_for_update().get(pk=lock_id)
    if name == 'prepare_task':
        data, parent = new_task_data(user, args)
        if parent and snapshot(parent) != proposal.snapshot:
            raise ValidationError('Asosiy topshiriq o‘zgargan. Taqsimotni qayta tayyorlang.')
        task = create_task(user, data, parent)
        text = task.code + ' yaratildi va ijrochiga xabar yuborildi.'
    else:
        task, due = change_data(user, args)
        if snapshot(task) != proposal.snapshot:
            raise ValidationError('Topshiriq o‘zgargan. Amalni yangidan tekshirib tayyorlang.')
        task = task_action(user, task.pk, args.action, args.text, due)
        text = ACTION_LABELS[args.action] + ' bajarildi. ' + task.code
    result = {'message': text, 'proposal': None, 'task_id': task.pk,
              'navigation': {'url': task.get_absolute_url(), 'label': task.code}}
    proposal.state, proposal.result = 'completed', result
    proposal.save(update_fields=['state', 'result'])
    return result


def execute(user, conversation, name, raw, references):
    if name not in TOOLS:
        raise ValidationError('Bunday agent amali mavjud emas.')
    args = TOOLS[name][0].model_validate(raw)
    if name in ('read_structure', 'navigate_account'):
        from . import agent_admin
        return agent_admin.read_or_navigate(user, name, args)
    if name == 'search_tasks':
        tasks = filtered(user, args.query, args.status, args.assignee_id)
        count = tasks.count()
        batch = list(tasks[(args.page-1)*12:args.page*12])
        references.update(t.pk for t in batch)
        return {'total': count, 'page': args.page, 'tasks': [row(t) for t in batch]}
    if name == 'get_task':
        task = get_task(user, args.task_id)
        references.add(task.pk)
        children = list(visible(user).filter(parent=task)[:20])
        references.update(t.pk for t in children)
        pending = task.deadline_requests.filter(state='pending').first()
        return {**row(task), 'description': task.description[:6000], 'issuer': task.issuer.full_name,
                'permitted_actions': actions_for(user, task), 'can_delegate': can_delegate(user, task),
                'children': [row(t) for t in children],
                'pending_deadline': {'due_at': pending.proposed_due_at.isoformat(), 'reason': pending.reason[:1500]} if pending else None,
                'history': [{'kind': e.get_kind_display(), 'text': e.body[:1500], 'time': e.created_at.isoformat()} for e in task.events.all()[:8]]}
    if name == 'get_summary':
        counts = {key: filtered(user, status=key).count() for key in ['active', 'overdue', 'soon', 'submitted', 'accepted', 'undated']}
        overdue = list(filtered(user, status='overdue').order_by('due_at')[:5])
        references.update(t.pk for t in overdue)
        return {'counts': counts, 'overdue_tasks': [row(t) for t in overdue]}
    if name == 'list_people':
        eligible = set(assignees_for(user).values_list('pk', flat=True))
        result, matching = find_people(people(user).select_related('department').order_by('full_name', 'pk'), args.query)
        return {**matching, 'people': [{'id': p.pk, 'name': p.full_name, 'job_title': p.job_title,
                           'department': p.department.name if p.department else '', 'can_assign': p.pk in eligible} for p in result]}
    if name == 'navigate':
        result = navigate(user, args)
        if args.task_id is not None:
            references.add(args.task_id)
        return result
    if name.startswith('prepare_'):
        result = prepare(user, conversation, name, args)
        task_id = getattr(args, 'task_id', None) or getattr(args, 'parent_id', None)
        if task_id:
            references.add(task_id)
        return result
    if name == 'get_notifications':
        items = list(user.notifications.filter(task__in=visible(user)).select_related('task')[:15])
        references.update(item.task_id for item in items)
        return {'notifications': [{'text': n.title, 'task_id': n.task_id, 'code': n.task.code, 'unread': n.read_at is None} for n in items]}
    if name == 'recent_activity':
        items = list(Event.objects.filter(task__in=visible(user), created_at__gte=timezone.now()-timedelta(days=args.days)).select_related('task', 'actor')[:20])
        references.update(e.task_id for e in items)
        return {'events': [{'task_id': e.task_id, 'code': e.task.code, 'kind': e.get_kind_display(),
                           'text': e.body[:1000], 'actor': e.actor.full_name if e.actor else 'Tizim', 'time': e.created_at.isoformat()} for e in items]}
