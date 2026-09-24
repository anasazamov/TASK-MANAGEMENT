from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Department, Task, User
from .permissions import assignees_for, controllers_for
from .services import validate_deadline


class LoginForm(AuthenticationForm):
    username = forms.CharField(label='Login', widget=forms.TextInput(attrs={'placeholder': 'Loginingiz', 'autofocus': True}))
    password = forms.CharField(label='Parol', widget=forms.PasswordInput(attrs={'placeholder': 'Parolingiz'}))
    error_messages = {'invalid_login': 'Login yoki parol noto‘g‘ri.', 'inactive': 'Hisobingiz bloklangan.'}


class LocalDateTimeInput(forms.DateTimeInput):
    input_type = 'datetime-local'

    def __init__(self, **kwargs):
        super().__init__(format='%Y-%m-%dT%H:%M', **kwargs)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, **kwargs):
        super().__init__(widget=MultipleFileInput(attrs={'multiple': True}), **kwargs)

    def clean(self, data, initial=None):
        items = data if isinstance(data, (list, tuple)) else [data]
        return [super(MultipleFileField, self).clean(item, initial) for item in items if item not in (None, '')]


ROLE_CHOICES = [(User.Role.EMPLOYEE, 'Xodim'), (User.Role.HEAD, 'Bo‘lim boshlig‘i'),
                (User.Role.DEPUTY, 'Rais o‘rinbosari'), (User.Role.OFFICE, 'Devonxona mudiri'),
                (User.Role.SECRETARY, 'Kotiba')]
SUPERVISED_HELP = 'Faqat rais o‘rinbosari uchun: qaysi bo‘linmalarni nazorat qiladi. Ko‘p tanlash mumkin.'


def deputy_departments(form, data):
    """Supervised departments belong to a deputy and to nobody else."""
    if data.get('role') != User.Role.DEPUTY:
        data['supervised'] = Department.objects.none()
        return data
    if not data.get('supervised') and 'supervised' not in form.data and form.instance.pk:
        # A caller that never offered the field, such as the agent editing a job
        # title, is not asking for the departments to be cleared.
        data['supervised'] = form.instance.supervised.all()
    if not data.get('supervised'):
        raise ValidationError('Rais o‘rinbosari uchun kamida bitta bo‘linma tanlang.')
    return data


class TaskForm(forms.ModelForm):
    due_at = forms.DateTimeField(label='Muddat sanasi va vaqti', required=False, widget=LocalDateTimeInput(),
                                 help_text='Toshkent vaqti. Bo‘sh qoldirilsa — muddatsiz topshiriq.')
    files = MultipleFileField(label='Fayllar', required=False,
                              help_text='Xat nusxasi yoki hujjatlar. Bir nechta fayl tanlash mumkin.')

    class Meta:
        model = Task
        fields = ['title', 'description', 'assignee', 'due_at', 'letter_number', 'letter_date', 'letter_sender']
        labels = {'assignee': 'Ijrochi'}
        widgets = {'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Kutilayotgan natija va qo‘shimcha talablar…'}),
                   'title': forms.TextInput(attrs={'placeholder': 'Nima bajarilishi kerak?'}),
                   'letter_number': forms.TextInput(attrs={'placeholder': 'Masalan: 01-12/345'}),
                   'letter_date': forms.DateInput(attrs={'type': 'date'}),
                   'letter_sender': forms.TextInput(attrs={'placeholder': 'Xat kelgan tashkilot yoki shaxs'})}

    def __init__(self, *args, user, parent=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent = parent
        self.fields['assignee'].queryset = assignees_for(user)
        self.fields['assignee'].empty_label = 'Ijrochini tanlang'
        self.fields['assignee'].label_from_instance = lambda u: f'{u.short_name} — {u.job_title}'
        for name in ('letter_number', 'letter_date', 'letter_sender'):
            # Incoming letters are registered by the chancellery; nobody else fills these.
            if user.is_office:
                self.fields[name].required = False
            else:
                del self.fields[name]

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


class ParticipantForm(forms.Form):
    person = forms.ModelChoiceField(queryset=User.objects.none(), label='Qo‘shimcha ijrochi', empty_label='Xodimni tanlang')
    part = forms.CharField(label='Ijro qismi', max_length=240, strip=True,
                           widget=forms.TextInput(attrs={'placeholder': 'Masalan: smeta hisob-kitobi'}))

    def __init__(self, *args, user, task, **kwargs):
        super().__init__(*args, **kwargs)
        taken = task.participants.values('user')
        self.fields['person'].queryset = assignees_for(user).exclude(
            pk__in=[task.assignee_id, task.issuer_id]).exclude(pk__in=taken)
        self.fields['person'].label_from_instance = lambda u: f'{u.short_name} — {u.job_title}'


class ControllerForm(forms.Form):
    person = forms.ModelChoiceField(queryset=User.objects.none(), label='Nazoratchi', empty_label='Xodimni tanlang')
    note = forms.CharField(label='Nazorat yo‘nalishi', max_length=240, required=False, strip=True,
                           widget=forms.TextInput(attrs={'placeholder': 'Ixtiyoriy: nimani kuzatadi'}))

    def __init__(self, *args, user, task, **kwargs):
        super().__init__(*args, **kwargs)
        taken = task.participants.values('user')
        self.fields['person'].queryset = controllers_for(user).exclude(
            pk__in=[task.assignee_id, task.issuer_id]).exclude(pk__in=taken)
        self.fields['person'].label_from_instance = lambda u: f'{u.short_name} — {u.job_title}'


class AttachmentForm(forms.Form):
    file = forms.FileField(label='Fayl (xat nusxasi yoki hujjat)')


class PartActionForm(forms.Form):
    action = forms.ChoiceField(choices=[(a, a) for a in ['submit_part', 'accept_part', 'return_part', 'remove']])
    text = forms.CharField(required=False, max_length=10000, strip=True)


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name']
        widgets = {'name': forms.TextInput(attrs={'placeholder': 'Masalan: Raqamlashtirish va IT'})}


class EmployeeForm(UserCreationForm):
    class Meta:
        model = User
        fields = ['full_name', 'username', 'job_title', 'phone', 'department', 'role', 'supervised',
                  'password1', 'password2']
        labels = {'username': 'Login', 'department': 'Bo‘linma'}
        help_texts = {'username': 'Lotin harflari, raqamlar va @/./+/-/_ belgilaridan foydalaning.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].choices = ROLE_CHOICES
        self.fields['supervised'].help_text = SUPERVISED_HELP
        self.fields['department'].required = True
        self.fields['password1'].label = 'Vaqtinchalik parol'
        self.fields['password2'].label = 'Parolni takrorlang'

    def clean(self):
        data = super().clean()
        department = data.get('department')
        if data.get('role') == User.Role.HEAD and department and department.head_id:
            raise ValidationError('Bu bo‘linmada boshliq mavjud. Avval amaldagi boshliq rolini o‘zgartiring.')
        return deputy_departments(self, data)

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
        fields = ['full_name', 'job_title', 'phone', 'department', 'role', 'supervised']
        labels = {'department': 'Bo‘linma'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].choices = ROLE_CHOICES
        self.fields['supervised'].help_text = SUPERVISED_HELP
        self.fields['department'].required = True

    def clean(self):
        data = super().clean()
        department = data.get('department')
        if data.get('role') == User.Role.HEAD and department and department.head_id not in [None, self.instance.pk]:
            raise ValidationError('Bu bo‘linmada boshqa boshliq mavjud.')
        if department and department.pk != self.instance.department_id:
            if self.instance.assigned_tasks.exclude(status='accepted').exists() or self.instance.issued_tasks.exclude(status='accepted').exists():
                raise ValidationError('Bo‘linmani almashtirishdan oldin xodimning faol topshiriqlarini yoping.')
        return deputy_departments(self, data)

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
