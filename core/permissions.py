from .models import User


def assignees_for(user):
    users = User.objects.filter(is_active=True).exclude(pk=user.pk).exclude(role=User.Role.CHAIR)
    if user.is_chair:
        return users
    if user.role == User.Role.HEAD and user.department_id:
        return users.filter(department_id=user.department_id, role=User.Role.EMPLOYEE)
    return users.none()


def can_manage(user, task):
    return user.is_active and (user.is_chair or task.issuer_id == user.pk) and task.assignee_id != user.pk


def can_add_participants(user, task):
    return can_manage(user, task) and task.status != 'accepted'


def can_delegate(user, task):
    return user.can_assign and task.assignee_id == user.pk and task.status == 'active'
