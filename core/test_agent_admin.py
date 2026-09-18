from django.test import TestCase, override_settings
from django.core.exceptions import PermissionDenied, ValidationError
from .models import User, Department, Task, AgentConversation, Event
from . import agent_tools as tools

@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AgentAdministrationTests(TestCase):
 def setUp(self):
  self.chair=User.objects.create_user('chair',role='chair')
  self.department=Department.objects.create(name='Loyiha')
  self.worker=User.objects.create_user('worker',department=self.department,full_name='Ali')
  self.conversation=AgentConversation.objects.create(user=self.chair)
 def proposal(self,name,args):
  result=tools.prepare(self.chair,self.conversation,name,args)
  return result['proposal']['id']
 def employee_args(self,**changes):
  data=dict(employee_id=self.worker.pk,username=None,full_name=None,job_title=None,department_id=None,role=None,is_active=None)
  return tools.EmployeeChange(**(data|changes))
 def test_employee_edit_preview_confirm_and_idempotency(self):
  args=self.employee_args(full_name='Ali Valiyev',is_active=False)
  pk=self.proposal('prepare_employee',args)
  self.worker.refresh_from_db();self.assertTrue(self.worker.is_active);self.assertEqual(self.worker.full_name,'Ali')
  result=tools.confirm(self.chair,self.conversation,pk)
  self.worker.refresh_from_db();self.assertFalse(self.worker.is_active);self.assertEqual(self.worker.full_name,'Ali Valiyev')
  self.assertEqual(tools.confirm(self.chair,self.conversation,pk),result)
 def test_employee_create_has_no_chat_password_and_secure_setup(self):
  args=self.employee_args(employee_id=None,username='new-user',full_name='Yangi Xodim',department_id=self.department.pk,role='employee')
  pk=self.proposal('prepare_employee',args)
  self.assertFalse(User.objects.filter(username='new-user').exists())
  result=tools.confirm(self.chair,self.conversation,pk)
  new=User.objects.get(username='new-user');self.assertFalse(new.has_usable_password())
  self.assertEqual(result['navigation']['url'],f'/employees/{new.pk}/password/')
 def test_department_preview_and_rename(self):
  args=tools.DepartmentChange(department_id=self.department.pk,name='Yangi bo‘lim')
  pk=self.proposal('prepare_department',args)
  self.department.refresh_from_db();self.assertEqual(self.department.name,'Loyiha')
  tools.confirm(self.chair,self.conversation,pk)
  self.department.refresh_from_db();self.assertEqual(self.department.name,args.name)
 def test_stale_and_revoked_permission_block_confirmation(self):
  pk=self.proposal('prepare_employee',self.employee_args(job_title='Bosh mutaxassis'))
  User.objects.filter(pk=self.worker.pk).update(full_name='Yangilangan')
  with self.assertRaises(ValidationError):tools.confirm(self.chair,self.conversation,pk)
  pk=self.proposal('prepare_employee',self.employee_args(job_title='Bosh mutaxassis'))
  self.chair.role='employee';self.chair.save()
  with self.assertRaises(PermissionDenied):tools.confirm(self.chair,self.conversation,pk)
 def test_employee_cannot_read_structure_or_modify_accounts(self):
  with self.assertRaises(PermissionDenied):tools.execute(self.worker,self.conversation,'read_structure',{},set())
  with self.assertRaises(PermissionDenied):tools.prepare(self.worker,self.conversation,'prepare_employee',self.employee_args(is_active=False))
 def test_task_edit_dry_run_has_no_notifications_or_events(self):
  task=Task.objects.create(issuer=self.chair,assignee=self.worker,title='Eski')
  args=tools.TaskEdit(task_id=task.pk,title='Yangi',description=None,assignee_id=None)
  pk=self.proposal('prepare_task_edit',args)
  task.refresh_from_db();self.assertEqual(task.title,'Eski');self.assertFalse(Event.objects.exists())
  tools.confirm(self.chair,self.conversation,pk)
  task.refresh_from_db();self.assertEqual(task.title,'Yangi');self.assertEqual(Event.objects.count(),1)
 def test_notification_read_only_changes_owners_visible_notifications(self):
  from .models import Notification
  task=Task.objects.create(issuer=self.chair,assignee=self.worker,title='Xabar')
  own=Notification.objects.create(user=self.chair,task=task,title='Mening xabarim')
  other=Notification.objects.create(user=self.worker,task=task,title='Boshqa xabar')
  pk=self.proposal('prepare_notifications_read',tools.Arguments())
  own.refresh_from_db();self.assertIsNone(own.read_at)
  tools.confirm(self.chair,self.conversation,pk)
  own.refresh_from_db();other.refresh_from_db()
  self.assertIsNotNone(own.read_at);self.assertIsNone(other.read_at)
