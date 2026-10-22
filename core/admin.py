"""Superuser-only view of the data. Workflow records stay read-only here.

Agent conversations, proposals and voice profiles are deliberately absent: they
hold transcripts and biometric data that no administration screen needs. Enrolled
faces are shown (photo only, never the embedding) because attendance depends on them.
"""
from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from . import attendance as attendance_service
from .models import (AccessCard, AccessEvent, Attendance, AttendanceAlert, AttendanceExcuse,
                     DeadlineRequest, Department, Event, ExcuseRequest, FaceEnrollment, FaceReview,
                     GeneratedPage, Notification, PushSubscription, Task, TaskAttachment, TaskParticipant,
                     User)


class ReadOnly:
    """Changes belong to the services, which check roles and write history."""
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TaskOwned(ReadOnly):
    """Removable only along with the task it belongs to, never on its own.

    Deleting a task asks Django whether everything hanging off it may go too,
    and that question is answered yes only while the task's own delete page or
    changelist is the view being served.
    """
    def has_delete_permission(self, request, obj=None):
        match = request.resolver_match
        return bool(match and match.url_name in ('core_task_delete', 'core_task_changelist'))


class AccessCardInline(admin.TabularInline):
    model = AccessCard
    fields = ['card_no', 'device_employee_no', 'label', 'active']
    extra = 0
    verbose_name = 'Domofon kartasi / ID'
    verbose_name_plural = 'Domofon kartalari va ID'


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    inlines = [AccessCardInline]
    fieldsets = UserAdmin.fieldsets + (
        ('Tashkilot', {'fields': ('full_name', 'job_title', 'role', 'department', 'supervised',
                                  'phone', 'telegram_id')}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Tashkilot', {'fields': ('full_name', 'job_title', 'role', 'department', 'phone')}),)
    readonly_fields = ['telegram_id']
    filter_horizontal = ['supervised']
    list_display = ['username', 'full_name', 'role', 'department', 'phone', 'linked', 'is_active']
    list_filter = ['role', 'is_active', 'department']
    search_fields = ['username', 'full_name', 'phone']
    ordering = ['full_name']

    @admin.display(boolean=True, description='Telegram')
    def linked(self, item):
        return item.telegram_id is not None


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'head', 'staff']
    search_fields = ['name']

    @admin.display(description='Xodimlar')
    def staff(self, item):
        return item.employees.count()


@admin.register(Task)
class TaskAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ['code', 'title', 'assignee', 'issuer', 'status', 'due_at', 'letter_number']
    list_filter = ['status', 'assignee__department']
    search_fields = ['title', 'description', 'letter_number', 'assignee__full_name']
    date_hierarchy = 'created_at'

    def has_delete_permission(self, request, obj=None):
        # A task entered by mistake can be removed here; its history, comments,
        # deadline requests, participants and notifications go with it. A task
        # that others were built on top of is still refused: delete those first.
        return True


@admin.register(TaskParticipant)
class TaskParticipantAdmin(TaskOwned, admin.ModelAdmin):
    list_display = ['task', 'user', 'kind', 'part', 'status', 'created_at']
    list_filter = ['kind', 'status']
    search_fields = ['user__full_name', 'part']


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(TaskOwned, admin.ModelAdmin):
    list_display = ['name', 'task', 'size_label', 'uploaded_by', 'created_at']
    search_fields = ['name', 'task__title']


@admin.register(Event, DeadlineRequest, Notification)
class WorkflowAdmin(TaskOwned, admin.ModelAdmin):
    pass


@admin.register(GeneratedPage)
class GeneratedPageAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ['title', 'user', 'updated_at']
    search_fields = ['title', 'user__full_name']

    def has_delete_permission(self, request, obj=None):
        return True  # An unwanted page should be removable without touching the database.


@admin.register(PushSubscription)
class PushSubscriptionAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ['user', 'endpoint', 'created_at', 'failed_at']
    list_filter = ['failed_at']
    search_fields = ['user__full_name']

    def has_delete_permission(self, request, obj=None):
        return True  # Revoking a browser's subscription is an administrative action.


# --- Attendance (door station) -------------------------------------------------
# Days and reviews are derived by core.attendance (settle_day, resolve_review), so
# here they are read-only and changed through actions that call those services:
# a hand edit would leave the day disagreeing with its own entries.

def photo_response(file):
    from .views import streamed
    if not file:
        raise Http404
    return streamed(FileResponse(file.open('rb'), content_type='image/jpeg'))


class PhotoAdmin:
    """Serves an object's photo to admins only, at <pk>/photo/."""
    photo_field = 'photo'

    def photo_file(self, obj):
        return getattr(obj, self.photo_field)

    def get_urls(self):
        name = f'{self.model._meta.app_label}_{self.model._meta.model_name}_photo'
        return [path('<int:pk>/photo/', self.admin_site.admin_view(self.photo_view), name=name)] + super().get_urls()

    def photo_view(self, request, pk):
        obj = get_object_or_404(self.model, pk=pk)
        if not self.has_view_permission(request, obj):
            raise Http404
        return photo_response(self.photo_file(obj))

    def photo_url(self, obj):
        return reverse(f'admin:{self.model._meta.app_label}_{self.model._meta.model_name}_photo', args=[obj.pk])

    @admin.display(description='Surat')
    def preview(self, obj):
        if not obj.pk or not self.photo_file(obj):
            return '—'
        url = self.photo_url(obj)
        return format_html('<a href="{}" target="_blank"><img src="{}" style="height:120px;border-radius:4px"></a>',
                           url, url)


@admin.register(AccessCard)
class AccessCardAdmin(admin.ModelAdmin):
    fields = ['user', 'card_no', 'device_employee_no', 'label', 'active']
    list_display = ['user', 'card_no', 'device_employee_no', 'label', 'active', 'last_seen']
    list_editable = ['active']
    list_filter = ['active']
    search_fields = ['user__full_name', 'user__username', 'card_no', 'device_employee_no', 'label']
    autocomplete_fields = ['user']
    actions = ['activate', 'deactivate']

    @admin.display(description='Oxirgi kirish')
    def last_seen(self, card):
        event = card.events.order_by('-happened_at').first()
        return timezone.localtime(event.happened_at).strftime('%d.%m.%Y %H:%M') if event else '—'

    @admin.action(description='Faollashtirish')
    def activate(self, request, queryset):
        self.message_user(request, f'{queryset.update(active=True)} ta karta faollashtirildi.')

    @admin.action(description='O‘chirib qo‘yish (davomat yozilmaydi)')
    def deactivate(self, request, queryset):
        self.message_user(request, f'{queryset.update(active=False)} ta karta o‘chirib qo‘yildi.')


class UnmatchedFilter(admin.SimpleListFilter):
    title = 'Xodim'
    parameter_name = 'matched'

    def lookups(self, request, model_admin):
        return [('yes', 'Tanilgan'), ('no', 'Noma’lum karta')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(user__isnull=False)
        if self.value() == 'no':
            return queryset.filter(user__isnull=True)
        return queryset


@admin.register(AccessEvent)
class AccessEventAdmin(PhotoAdmin, ReadOnly, admin.ModelAdmin):
    """The raw door log; an audit record, so nothing here is edited or deleted."""
    list_display = ['happened_at', 'person', 'device_name', 'card_no', 'employee_no', 'minor', 'confidence',
                    'has_photo', 'review_state']
    list_filter = [UnmatchedFilter, 'minor', 'door_no']
    search_fields = ['user__full_name', 'device_name', 'card_no', 'employee_no']
    date_hierarchy = 'happened_at'
    list_select_related = ['user', 'face_review']
    exclude = ['photo']
    readonly_fields = ['preview']

    @admin.display(description='Xodim', ordering='user__full_name')
    def person(self, event):
        return event.user or format_html('<span style="color:#b45309">Noma’lum</span>')

    @admin.display(description='Moslik', ordering='face_confidence')
    def confidence(self, event):
        return f'{event.face_confidence:.2f}' if event.face_confidence is not None else '—'

    @admin.display(description='Surat', boolean=True)
    def has_photo(self, event):
        return bool(event.photo)

    @admin.display(description='Tekshiruv')
    def review_state(self, event):
        review = getattr(event, 'face_review', None)
        return review.get_state_display() if review else '—'


class ReviewInline(admin.TabularInline):
    model = FaceReview
    fields = ['created_at', 'confidence', 'state', 'reason', 'resolved_by', 'resolved_at']
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Attendance)
class AttendanceAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ['date', 'user', 'status', 'entry_count', 'first', 'last', 'late']
    list_filter = ['status', 'user__department']
    search_fields = ['user__full_name', 'user__username']
    date_hierarchy = 'date'
    list_select_related = ['user']
    inlines = [ReviewInline]
    actions = ['resettle']

    @admin.display(description='Birinchi kirish', ordering='first_entry_at')
    def first(self, day):
        return timezone.localtime(day.first_entry_at).strftime('%H:%M') if day.first_entry_at else '—'

    @admin.display(description='Oxirgi kirish', ordering='last_entry_at')
    def last(self, day):
        return timezone.localtime(day.last_entry_at).strftime('%H:%M') if day.last_entry_at else '—'

    @admin.display(description='Kechikish')
    def late(self, day):
        minutes = attendance_service.late_minutes(day)
        return attendance_service.duration_label(minutes) if minutes else '—'

    @admin.action(description='Kunni kirishlardan qayta hisoblash')
    def resettle(self, request, queryset):
        with transaction.atomic():
            for day in queryset.select_related('user'):
                attendance_service.settle_day(day)
        self.message_user(request, f'{queryset.count()} kun qayta hisoblandi.')


class RejectForm(forms.Form):
    reason = forms.CharField(label='Rad etish sababi (audit uchun saqlanadi, xodimga yuboriladi)',
                             widget=forms.Textarea(attrs={'rows': 3, 'cols': 60}))


@admin.register(FaceReview)
class FaceReviewAdmin(PhotoAdmin, ReadOnly, admin.ModelAdmin):
    """Decisions go through resolve_review, which re-settles the day and warns the employee."""
    list_display = ['created_at', 'user', 'confidence_label', 'state', 'reason', 'resolved_by', 'resolved_at']
    list_filter = ['state']
    search_fields = ['user__full_name', 'reason']
    date_hierarchy = 'created_at'
    list_select_related = ['user', 'resolved_by', 'event']
    readonly_fields = ['preview']
    actions = ['approve', 'reject']

    def photo_file(self, review):
        return review.event.photo

    @admin.display(description='Moslik', ordering='confidence')
    def confidence_label(self, review):
        return f'{review.confidence:.2f}' if review.confidence is not None else 'yuz yo‘q'

    def _resolve(self, request, queryset, approve, reason=''):
        done, failed = 0, []
        for review in queryset.filter(state=FaceReview.State.PENDING):
            try:
                with transaction.atomic():
                    attendance_service.resolve_review(request.user, review.pk, approve, reason)
                done += 1
            except ValidationError as error:
                failed.append(f'{review.user}: {" ".join(error.messages)}')
        skipped = queryset.exclude(state=FaceReview.State.PENDING).count()
        self.message_user(request, f'{done} ta tekshiruv {"tasdiqlandi" if approve else "rad etildi"}.'
                          + (f' {skipped} tasi allaqachon hal qilingan edi.' if skipped else ''))
        for line in failed:
            self.message_user(request, line, messages.ERROR)

    @admin.action(description='Tasdiqlash (yo‘qlama hisobga olinadi)')
    def approve(self, request, queryset):
        self._resolve(request, queryset, True)

    @admin.action(description='Rad etish (sabab so‘raladi)')
    def reject(self, request, queryset):
        form = RejectForm(request.POST if 'reason' in request.POST else None)
        if form.is_valid():
            self._resolve(request, queryset, False, form.cleaned_data['reason'])
            return None
        return render(request, 'admin/core/facereview/reject.html', {
            **self.admin_site.each_context(request), 'title': 'Tekshiruvni rad etish', 'form': form,
            'reviews': queryset, 'opts': self.model._meta,
            'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME})


class ExcuseForm(forms.ModelForm):
    class Meta:
        model = AttendanceExcuse
        fields = ['user', 'date', 'kind', 'note', 'expected_at']

    def clean(self):
        data = super().clean()
        kind = data.get('kind')
        if kind == AttendanceExcuse.Kind.OTHER and not (data.get('note') or '').strip():
            self.add_error('note', '«Boshqa sabab» uchun izoh yozing.')
        if kind != AttendanceExcuse.Kind.LATE:
            data['expected_at'] = None
        return data


@admin.register(AttendanceExcuse)
class AttendanceExcuseAdmin(admin.ModelAdmin):
    form = ExcuseForm
    list_display = ['date', 'user', 'kind', 'note', 'expected_at', 'created_by', 'created_at']
    list_filter = ['kind']
    search_fields = ['user__full_name', 'note']
    date_hierarchy = 'date'
    autocomplete_fields = ['user']

    def save_model(self, request, obj, form, change):
        obj.created_by = request.user   # who entered it last, for audit
        super().save_model(request, obj, form, change)


@admin.register(ExcuseRequest)
class ExcuseRequestAdmin(ReadOnly, admin.ModelAdmin):
    """Employees' own requests. Decided on the Uzr so'rovlari page, which also tells them."""
    list_display = ['created_at', 'user', 'period_label', 'kind', 'state', 'reason', 'resolved_by', 'resolved_at']
    list_filter = ['state', 'kind']
    search_fields = ['user__full_name', 'note', 'reason']
    date_hierarchy = 'created_at'
    list_select_related = ['user', 'resolved_by']

    @admin.display(description='Kunlar', ordering='start')
    def period_label(self, item):
        return item.period_label


class EnrollmentForm(forms.ModelForm):
    image = forms.FileField(label='Yuz surati', required=False,
                             help_text='Aniq, yorug‘, to‘g‘ri qaragan surat. Yuz topilmasa saqlanmaydi.')

    class Meta:
        model = FaceEnrollment
        fields = ['user']

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if not image:
            if not self.instance.pk:
                raise ValidationError('Surat yuklang.')
            return image
        # Check for a face now, so a photo without one is a form error, not a lost save.
        from . import face
        found = face.embed(image.read())
        image.seek(0)
        if found is None:
            raise ValidationError('Suratda yuz aniqlanmadi yoki yuz tizimi mavjud emas. '
                                  'Aniq, yorug‘ va to‘g‘ri qaragan surat yuklang.')
        return image


@admin.register(FaceEnrollment)
class FaceEnrollmentAdmin(PhotoAdmin, admin.ModelAdmin):
    """The reference faces. The embedding itself is never shown or edited."""
    form = EnrollmentForm
    list_display = ['user', 'model', 'updated_at']
    search_fields = ['user__full_name', 'user__username']
    autocomplete_fields = ['user']

    def get_readonly_fields(self, request, obj=None):
        # A face belongs to one person: to move it, delete it and enroll the other one.
        return ['user', 'preview', 'model', 'updated_at'] if obj else []

    def save_model(self, request, obj, form, change):
        image = form.cleaned_data.get('image')
        if image:
            saved = attendance_service.enroll_face(obj.user, image)
            obj.pk = saved.pk   # the admin's "saved" message and redirect need it


@admin.register(AttendanceAlert)
class AttendanceAlertAdmin(ReadOnly, admin.ModelAdmin):
    """Once-a-day markers. Deleting one lets that alert go out again on the next run."""
    list_display = ['date', 'kind', 'subject', 'created_at']
    list_filter = ['kind']
    search_fields = ['subject__full_name']
    date_hierarchy = 'date'

    def has_delete_permission(self, request, obj=None):
        return True


admin.site.site_header = 'Topshiriq nazorati · Administratsiya'
admin.site.site_title = 'Topshiriq nazorati'
admin.site.index_title = 'Ma’lumotlar'
