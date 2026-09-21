from django.db.models import Q

from .models import TaskParticipant, User


def assignees_for(user):
    users = User.objects.filter(is_active=True).exclude(pk=user.pk).exclude(role=User.Role.CHAIR)
    if user.is_chair or user.is_secretary:
        # The secretary writes up the chair's instructions for every department.
        return users
    if user.is_office:
        # The office forwards incoming letters to department heads, not to staff.
        return users.filter(role=User.Role.HEAD)
    if user.role == User.Role.HEAD and user.department_id:
        return users.filter(department_id=user.department_id, role=User.Role.EMPLOYEE)
    return users.none()


def controllers_for(user):
    """Who this manager may ask to follow a task: own staff, the office and secretaries."""
    people = User.objects.filter(is_active=True).exclude(pk=user.pk)
    if user.is_chair or user.is_office:
        return people
    if user.role == User.Role.HEAD:
        return people.filter(Q(department_id=user.department_id) | Q(role__in=[User.Role.SECRETARY, User.Role.OFFICE]))
    return people.none()


def can_manage(user, task):
    return user.is_active and (user.is_chair or task.issuer_id == user.pk) and task.assignee_id != user.pk


def can_control(user, task):
    """Controllers follow execution: they may comment and ask for a report."""
    return user.is_active and task.participants.filter(
        user=user, kind=TaskParticipant.Kind.CONTROLLER).exists()


def can_add_participants(user, task):
    return can_manage(user, task) and task.status != 'accepted'


def can_attach(user, task):
    return user.is_active and task.status != 'accepted' and (
        can_manage(user, task) or task.assignee_id == user.pk
        or task.participants.filter(user=user).exists())


def can_delegate(user, task):
    return user.can_assign and task.assignee_id == user.pk and task.status == 'active'
