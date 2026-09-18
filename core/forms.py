from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Department, Task, User
from .permissions import assignees_for
from .services import validate_deadline


class LoginForm(AuthenticationForm):
    username = forms.CharField(label='Login', widget=forms.TextInput(attrs={'placeholder': 'Loginingiz', 'autofocus': True}))
    password = forms.CharField(label='Parol', widget=forms.PasswordInput(attrs={'placeholder': 'Parolingiz'}))
    error_messages = {'invalid_login': 'Login yoki parol noto‘g‘ri.', 'inactive': 'Hisobingiz bloklangan.'}


class LocalDateTimeInput(forms.DateTimeInput):
    input_type = 'datetime-local'

    def __init__(self, **kwargs):
        super().__init__(format='%Y-%m-%dT%H:%M', **kwargs)


class TaskForm(forms.ModelForm):
    due_at = forms.DateTimeField(label='Muddat sanasi va vaqti', required=False, widget=LocalDateTimeInput(),
                                 help_text='Toshkent vaqti. Bo‘sh qoldirilsa — muddatsiz topshiriq.')

    class Meta:
        model = Task
        fields = ['title', 'description', 'assignee', 'due_at']
        labels = {'assignee': 'Ijrochi'}
        widgets = {'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Kutilayotgan natija va qo‘shimcha talablar…'}),
                   'title': forms.TextInput(attrs={'placeholder': 'Nima bajarilishi kerak?'})}

    def __init__(self, *args, user, parent=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent = parent
        self.fields['assignee'].queryset = assignees_for(user)
        self.fields['assignee'].empty_label = 'Ijrochini tanlang'
        self.fields['assignee'].label_from_instance = lambda u: f'{u.short_name} — {u.job_title}'

    def clean(self):
        data = super().clean()
        if 'due_at' in data:
            validate_deadline(data['due_at'], parent=self.parent)
        return data


class ActionForm(forms.Form):
    action = forms.ChoiceField(choices=[(a, a) for a in [
        'comment', 'submit', 'accept', 'return', 'set_deadline', 'request_deadline',
        'approve_deadline', 'reject_deadline', 'request_report', 'report',
    ]])
    text = forms.CharField(required=False, max_length=10000, strip=True)
    due_at = forms.DateTimeField(required=False, widget=LocalDateTimeInput())


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name']
        widgets = {'name': forms.TextInput(attrs={'placeholder': 'Masalan: Raqamlashtirish va IT'})}


class EmployeeForm(UserCreationForm):
    class Meta:
        model = User
        fields = ['full_name', 'username', 'job_title', 'department', 'role', 'password1', 'password2']
        labels = {'username': 'Login', 'department': 'Bo‘linma'}
        help_texts = {'username': 'Lotin harflari, raqamlar va @/./+/-/_ belgilaridan foydalaning.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].choices = [(User.Role.EMPLOYEE, 'Xodim'), (User.Role.HEAD, 'Bo‘lim boshlig‘i')]
        self.fields['department'].required = True
        self.fields['password1'].label = 'Vaqtinchalik parol'
        self.fields['password2'].label = 'Parolni takrorlang'

    def clean(self):
        data = super().clean()
        department = data.get('department')
        if data.get('role') == User.Role.HEAD and department and department.head_id:
            raise ValidationError('Bu bo‘linmada boshliq mavjud. Avval amaldagi boshliq rolini o‘zgartiring.')
        return data

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit and user.role == User.Role.HEAD:
            department = Department.objects.select_for_update().get(pk=user.department_id)
            if department.head_id:
                raise ValidationError('Bo‘linmaga boshliq allaqachon biriktirilgan.')
            department.head = user
            department.save()
        return user


class EmployeeEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['full_name', 'job_title', 'department', 'role']
        labels = {'department': 'Bo‘linma'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].choices = [(User.Role.EMPLOYEE, 'Xodim'), (User.Role.HEAD, 'Bo‘lim boshlig‘i')]
        self.fields['department'].required = True

    def clean(self):
        data = super().clean()
        department = data.get('department')
        if data.get('role') == User.Role.HEAD and department and department.head_id not in [None, self.instance.pk]:
            raise ValidationError('Bu bo‘linmada boshqa boshliq mavjud.')
        if department and department.pk != self.instance.department_id:
            if self.instance.assigned_tasks.exclude(status='accepted').exists() or self.instance.issued_tasks.exclude(status='accepted').exists():
                raise ValidationError('Bo‘linmani almashtirishdan oldin xodimning faol topshiriqlarini yoping.')
        return data

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            Department.objects.filter(head=user).update(head=None)
            if user.role == User.Role.HEAD:
                department = Department.objects.select_for_update().get(pk=user.department_id)
                if department.head_id not in [None, user.pk]:
                    raise ValidationError('Bo‘linmaga boshqa boshliq biriktirilgan.')
                department.head = user
                department.save()
        return user
