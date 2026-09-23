"""Superuser-only view of the data. Workflow records stay read-only here.

Agent conversations, proposals and voice profiles are deliberately absent: they
hold transcripts and biometric data that no administration screen needs.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (DeadlineRequest, Department, Event, GeneratedPage, Notification,
                     PushSubscription, Task, TaskAttachment, TaskParticipant, User)


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


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Tashkilot', {'fields': ('full_name', 'job_title', 'role', 'department', 'phone', 'telegram_id')}),)
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Tashkilot', {'fields': ('full_name', 'job_title', 'role', 'department', 'phone')}),)
    readonly_fields = ['telegram_id']
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


admin.site.site_header = 'Topshiriq nazorati · Administratsiya'
admin.site.site_title = 'Topshiriq nazorati'
admin.site.index_title = 'Ma’lumotlar'
