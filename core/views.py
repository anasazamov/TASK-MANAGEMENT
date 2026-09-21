from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import reports
from .forms import ActionForm, DepartmentForm, EmployeeEditForm, EmployeeForm, LoginForm, TaskForm
from .models import DeadlineRequest, Department, Event, Task, User
from .permissions import can_delegate, can_manage
from .services import create_task, mark_seen, require, task_action


class SignInView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True



def tasks_for(user):
    return Task.objects.visible_to(user).enriched()


def with_escalations(tasks):
    tasks = list(tasks)
    by_id = {task.pk: task for task in tasks}
    for task in tasks:
        task.escalations = []
    for child in tasks:
        if child.state != 'overdue':
            continue
        parent_id, seen = child.parent_id, {child.pk}
        while parent_id in by_id and parent_id not in seen:
            seen.add(parent_id)
            parent = by_id[parent_id]
            parent.escalations.append(child)
            parent_id = parent.parent_id
    return tasks


def counts(tasks):
    result = {key: 0 for key in ['active', 'overdue', 'submitted', 'soon', 'undated', 'accepted', 'all']}
    for task in tasks:
        result['all'] += 1
        if task.status != 'accepted':
            result['active'] += 1
        if task.state != 'active':
            result[task.state] += 1
    return result


def workload(user):
    employees = User.objects.filter(pk__in=tasks_for(user).values('assignee_id')).select_related('department')
    now = timezone.now()
    employees = employees.annotate(
        active_count=Count('assigned_tasks', filter=~Q(assigned_tasks__status='accepted')),
        overdue_count=Count('assigned_tasks', filter=Q(assigned_tasks__status='active', assigned_tasks__due_at__lt=now)),
        soon_count=Count('assigned_tasks', filter=Q(assigned_tasks__status='active', assigned_tasks__due_at__gte=now, assigned_tasks__due_at__lte=now + timedelta(days=3))),
    ).order_by('-overdue_count', '-active_count', 'full_name')
    return employees


@login_required
def dashboard(request):
    tasks = with_escalations(tasks_for(request.user))
    task_counts = counts(tasks)
    attention = sorted([t for t in tasks if t.state in ['overdue', 'soon'] or t.is_stale or t.escalations],
                       key=lambda t: (0 if t.state == 'overdue' else 1 if t.state == 'soon' else 2, t.due_at or t.created_at))
    pending = [t for t in tasks if t.status == 'submitted' and can_manage(request.user, t)]
    extensions = DeadlineRequest.objects.filter(task__in=tasks_for(request.user), state='pending').select_related('task', 'requester')
    extensions = [r for r in extensions if can_manage(request.user, r.task)]
    undated = sorted([t for t in tasks if t.state == 'undated'], key=lambda t: t.created_at)
    return render(request, 'core/dashboard.html', {
        'page_title': 'Mening panelim' if request.user.role == 'employee' else 'Boshqaruv paneli',
        'subtitle': ('Butun jamiyat bo‘ylab ijro holati' if request.user.is_chair else
                     'Sizga berilgan topshiriqlar holati' if request.user.role == 'employee' else
                     str(request.user.department or 'Shaxsiy topshiriqlar') + ' — ijro holati'),
        'counts': task_counts, 'attention': attention[:8], 'attention_count': len(attention),
        'pending_tasks': pending, 'extensions': extensions, 'decision_count': len(pending) + len(extensions),
        'undated': undated[:4], 'undated_count': len(undated), 'employees': workload(request.user)[:5],
    })


def task_list_context(user, params):
    queryset = tasks_for(user)
    task_counts = counts(queryset)
    active_filter = params.get('filter', 'active')
    if active_filter not in ['active', 'all', 'overdue', 'submitted', 'soon', 'undated', 'accepted', 'attention']:
        active_filter = 'active'
    query = params.get('q', '').strip()[:200]
    if query:
        queryset = queryset.filter(Q(title__icontains=query) | Q(assignee__full_name__icontains=query) | Q(description__icontains=query))
    employee = params.get('employee', '')
    if employee.isdigit():
        queryset = queryset.filter(assignee_id=employee)
    # Escalations are computed before search so a parent's warning survives filtering.
    task_map = {t.pk: t for t in with_escalations(tasks_for(user))}
    tasks = [task_map[t.pk] for t in queryset]
    if active_filter == 'active':
        tasks = [t for t in tasks if t.status != 'accepted']
    elif active_filter == 'attention':
        tasks = [t for t in tasks if t.state in ['overdue', 'soon'] or t.is_stale or t.escalations]
    elif active_filter != 'all':
        tasks = [t for t in tasks if t.state == active_filter]
    sections = []
    for state, label in [('overdue', 'Muddati o‘tgan'), ('submitted', 'Tasdiq kutilmoqda'), ('soon', 'Muddati yaqinlashgan'),
                         ('active', 'Rejadagi'), ('undated', 'Muddatsiz — qat’iy sana belgilanmagan'), ('accepted', 'Qabul qilingan')]:
        rows = sorted([t for t in tasks if t.state == state], key=lambda t: t.due_at or t.created_at)
        if rows:
            sections.append({'state': state, 'title': label, 'tasks': rows})
    filters = [(key, label, task_counts[key]) for key, label in [('active', 'Faol'), ('overdue', 'Muddati o‘tgan'),
        ('submitted', 'Tasdiq kutilmoqda'), ('soon', 'Muddati yaqin'), ('undated', 'Muddatsiz'), ('accepted', 'Qabul qilingan'), ('all', 'Barchasi')]]
    return {'page_title': 'Topshiriqlar', 'subtitle': 'Barcha topshiriqlar va quyi taqsimotlar',
        'sections': sections, 'filters': filters, 'active_filter': active_filter, 'query': query, 'employee_filter': employee, 'total': len(tasks)}


@login_required
def task_list(request):
    return render(request, 'core/tasks.html', task_list_context(request.user, request.GET))

@login_required
def task_detail(request, pk):
    task = get_object_or_404(tasks_for(request.user), pk=pk)
    mark_seen(task, request.user)
    all_tasks = {t.pk: t for t in with_escalations(tasks_for(request.user))}
    task.escalations = all_tasks[task.pk].escalations
    ancestor_rows = [{'task': t, 'visible': tasks_for(request.user).filter(pk=t.pk).exists()} for t in task.ancestors()]
    template = 'core/_detail_content.html' if request.GET.get('panel') == '1' else 'core/detail.html'
    return render(request, template, {
        'page_title': 'Topshiriq tafsilotlari', 'subtitle': task.code, 'task': task,
        'can_manage': can_manage(request.user, task), 'can_delegate': can_delegate(request.user, task),
        'is_assignee': task.assignee_id == request.user.pk, 'ancestors': ancestor_rows,
        'children': tasks_for(request.user).filter(parent=task), 'events': task.events.select_related('actor'),
        'pending_request': task.deadline_requests.filter(state='pending').select_related('requester').first(),
    })


@login_required
def task_create(request):
    require(request.user.can_assign)
    parent = None
    if request.GET.get('parent'):
        if not request.GET['parent'].isdigit():
            raise Http404
        parent = get_object_or_404(tasks_for(request.user), pk=request.GET['parent'])
        require(can_delegate(request.user, parent))
    form = TaskForm(request.POST or None, user=request.user, parent=parent)
    if request.method == 'POST' and form.is_valid():
        try:
            task = create_task(request.user, {k: form.cleaned_data[k] for k in ['title', 'description', 'assignee', 'due_at']}, parent)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Topshiriq yaratildi va ijrochiga xabar yuborildi.')
            return redirect(task)
    return render(request, 'core/task_form.html', {'page_title': 'Topshiriqni taqsimlash' if parent else 'Yangi topshiriq',
        'subtitle': 'Ijrochi, mazmun va muddatni belgilang', 'form': form, 'parent': parent})


@login_required
@require_POST
def perform_action(request, pk):
    get_object_or_404(tasks_for(request.user), pk=pk)
    form = ActionForm(request.POST)
    if form.is_valid():
        try:
            task_action(request.user, pk, **form.cleaned_data)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        else:
            messages.success(request, 'Amal bajarildi. O‘zgarish tarixga yozildi.')
    else:
        messages.error(request, 'Ma’lumotlarni tekshiring. Sana, vaqt yoki matn noto‘g‘ri.')
    return redirect('task_detail', pk=pk)


@login_required
def employees(request):
    require(request.user.can_assign)
    week = request.GET.get('week', '')
    period = reports.parse_week(week)
    rows = reports.employee_stats(request.user, period)
    if request.GET.get('format') == 'xlsx':
        name = f"xodimlar-statistikasi-{week if period else 'barcha'}-{timezone.localdate():%Y%m%d}.xlsx"
        response = HttpResponse(reports.workbook(rows, period, request.user.short_name),
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{name}"'
        return response
    return render(request, 'core/employees.html', {'page_title': 'Xodimlar', 'subtitle': 'Topshiriq yuklamasi va muddat holati',
        'rows': rows, 'week': week if period else '', 'default_week': reports.current_week(), 'period_label': reports.period_label(period)})


@login_required
def structure(request):
    require(request.user.is_chair)
    form = DepartmentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Yangi bo‘linma qo‘shildi.')
        return redirect('structure')
    return render(request, 'core/structure.html', {'page_title': 'Struktura', 'subtitle': 'Bo‘linmalar va xodimlarni boshqarish',
        'form': form, 'departments': Department.objects.select_related('head').prefetch_related('employees')})


@login_required
def employee_edit(request, pk=None):
    require(request.user.is_chair)
    employee = get_object_or_404(User.objects.exclude(role='chair'), pk=pk) if pk else None
    form = EmployeeEditForm(request.POST or None, instance=employee) if employee else EmployeeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            form.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Xodim ma’lumotlari saqlandi.')
            return redirect('structure')
    return render(request, 'core/form.html', {'page_title': 'Xodimni tahrirlash' if employee else 'Xodim qo‘shish',
        'subtitle': 'Tashkilot xodimi va tizimga kirish huquqlari', 'form': form, 'back_url': 'structure', 'submit_label': 'Saqlash'})


@login_required
@require_POST
def employee_toggle(request, pk):
    require(request.user.is_chair)
    employee = get_object_or_404(User.objects.exclude(role='chair'), pk=pk)
    employee.is_active = not employee.is_active
    employee.save(update_fields=['is_active'])
    messages.success(request, 'Xodim hisobi faollashtirildi.' if employee.is_active else 'Xodim hisobi bloklandi.')
    return redirect('structure')


@login_required
def employee_password(request, pk):
    require(request.user.is_chair)
    employee = get_object_or_404(User.objects.exclude(role='chair'), pk=pk)
    form = SetPasswordForm(employee, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Yangi parol saqlandi. Oldingi sessiyalar bekor bo‘ladi.')
        return redirect('structure')
    return render(request, 'core/form.html', {'page_title': 'Parolni tiklash', 'subtitle': employee.full_name,
        'form': form, 'back_url': 'structure', 'submit_label': 'Yangi parolni saqlash'})


@login_required
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, 'Parolingiz yangilandi.')
        return redirect('dashboard')
    return render(request, 'core/form.html', {'page_title': 'Parolni o‘zgartirish', 'subtitle': 'Hisob xavfsizligi',
        'form': form, 'back_url': 'dashboard', 'submit_label': 'Saqlash'})


@login_required
def notifications(request):
    items = request.user.notifications.filter(task__in=tasks_for(request.user)).select_related('task')
    return render(request, 'core/notifications.html', {'page_title': 'Xabarnomalar', 'subtitle': 'Topshiriqlar bo‘yicha so‘nggi yangiliklar',
        'items': Paginator(items, 30).get_page(request.GET.get('page'))})


@login_required
@require_POST
def notifications_read(request):
    request.user.notifications.filter(task__in=tasks_for(request.user), read_at__isnull=True).update(read_at=timezone.now())
    return redirect('notifications')


@login_required
def timeline(request):
    events = Event.objects.filter(task__in=tasks_for(request.user)).select_related('actor', 'task')
    kind = request.GET.get('kind', '')
    if kind in Event.Kind.values:
        events = events.filter(kind=kind)
    days = request.GET.get('days', '30')
    if days in ['7', '30']:
        events = events.filter(created_at__gte=timezone.now() - timedelta(days=int(days)))
    return render(request, 'core/timeline.html', {'page_title': 'Harakatlar tarixi', 'subtitle': 'Kim, qachon va nima qildi',
        'events': Paginator(events, 30).get_page(request.GET.get('page')), 'kinds': Event.Kind.choices, 'kind': kind, 'days': days})


@login_required
def chains(request):
    require(request.user.can_assign)
    tasks = with_escalations(tasks_for(request.user))
    by_id = {task.pk: task for task in tasks}
    for task in tasks:
        task.tree_children = []
    roots = []
    for task in tasks:
        if task.parent_id in by_id:
            by_id[task.parent_id].tree_children.append(task)
        else:
            roots.append(task)
    return render(request, 'core/chains.html', {'page_title': 'Nazorat zanjiri', 'subtitle': 'Topshiriqlar va ularning quyi taqsimotlari', 'roots': roots})
