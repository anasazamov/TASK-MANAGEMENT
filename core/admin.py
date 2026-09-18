from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Department, User, Task, DeadlineRequest, Event, Notification


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (('Tashkilot', {'fields': ('full_name', 'job_title', 'role', 'department')}),)
    add_fieldsets = UserAdmin.add_fieldsets + (('Tashkilot', {'fields': ('full_name', 'job_title', 'role', 'department')}),)
    list_display = ['username', 'full_name', 'role', 'department', 'is_active']


@admin.register(Event, Task, DeadlineRequest, Notification)
class ReadOnlyWorkflowAdmin(admin.ModelAdmin):
    # Workflow records can only be changed through the permission-checked services.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Department)
admin.site.site_header = 'Topshiriq nazorati · Administratsiya'
