"""Application administration through scoped previews and confirmed transactions."""
import hashlib
import json
import secrets
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from .models import AgentProposal, Department, Event, Task, TaskParticipant, User
from .forms import DepartmentForm, EmployeeForm, EmployeeEditForm
from .permissions import assignees_for, can_manage
from .services import add_participant, log, notify, part_action, require


def validate(form):
    if not form.is_valid():
        raise ValidationError([str(e) for errors in form.errors.values() for e in errors])
    return form


def employee(user, pk):
    require(user.is_active and user.is_chair)
    result = User.objects.exclude(role='chair').filter(pk=pk).first()
    require(result is not None)
    return result


def read_or_navigate(user, name, args):
    require(user.is_active)
    if name == 'navigate_account':
        if args.page != 'password_change':
            require(user.is_chair)
        target = employee(user, args.employee_id) if args.page in ('employee_edit', 'employee_password') else None
        url = reverse(args.page, kwargs={'pk': target.pk} if target else None)
        label = {'password_change':'Parolni o‘zgartirish','employee_create':'Xodim qo‘shish',
                 'employee_edit':'Xodimni tahrirlash','employee_password':'Xodim parolini tiklash'}[args.page]
        return {'navigation': {'url':url,'label':label}, 'message':label+' sahifasini ochyapman.'}
    require(user.can_assign)
    departments = Department.objects.all() if user.is_chair else Department.objects.filter(pk=user.department_id)
    members = User.objects.all() if user.is_chair else User.objects.filter(department_id=user.department_id)
    return {'departments':list(departments.values('id','name','head_id')),
            'employees':list(members.values('id','full_name','username','job_title','department_id','role','is_active'))}


def version(user, name, args):
    if name == 'prepare_employee':
        require(user.is_chair)
        data = list(User.objects.filter(pk=args.employee_id).values('id','username','full_name','job_title','department_id','role','is_active'))
    elif name == 'prepare_department':
        require(user.is_chair)
        data = list(Department.objects.filter(pk=args.department_id).values('id','name','head_id'))
    elif name == 'prepare_task_edit':
        data = list(Task.objects.visible_to(user).filter(pk=args.task_id).values('id','updated_at','title','description','assignee_id','status'))
    elif name == 'prepare_participant':
        data = list(TaskParticipant.objects.filter(task_id=args.task_id).values('id','user_id','kind','status'))
    elif name == 'prepare_part_action':
        data = list(TaskParticipant.objects.filter(pk=args.participant_id).values('id','task_id','user_id','kind','status'))
    else:
        data = list(user.notifications.filter(read_at__isnull=True, task__in=Task.objects.visible_to(user)).values_list('pk',flat=True))
    return hashlib.sha256(json.dumps(data,default=str,sort_keys=True).encode()).hexdigest()


def apply(user, name, args):
    require(user.is_active)
    if name == 'prepare_department':
        require(user.is_chair)
        instance = Department.objects.select_for_update().filter(pk=args.department_id).first() if args.department_id else None
        if args.department_id:
            require(instance is not None)
        previous = instance.name if instance else ''
        item = validate(DepartmentForm({'name':args.name}, instance=instance)).save()
        return {'Amal':'Bo‘linmani tahrirlash' if args.department_id else 'Bo‘linma yaratish', 'Oldingi nom':previous,'Yangi nom':item.name}, {'message':item.name+' — bo‘linma saqlandi.', 'navigation':{'url':reverse('structure'),'label':'Struktura'}}
    if name == 'prepare_employee':
        require(user.is_chair)
        if args.employee_id:
            User.objects.select_for_update().filter(pk=args.employee_id).first()
            item = employee(user,args.employee_id)
            if args.username is not None and args.username != item.username:
                raise ValidationError('Mavjud xodim loginini o‘zgartirish qo‘llanmaydi.')
            before = f'{item.full_name}; {item.job_title}; {item.department}; {item.get_role_display()}; faol={item.is_active}'
            data = {key: getattr(args,key) if getattr(args,key) is not None else getattr(item,key)
                    for key in ['full_name','job_title','role']}
            data['phone'] = item.phone
            data['department'] = args.department_id if args.department_id is not None else item.department_id
            item = validate(EmployeeEditForm(data,instance=item)).save()
        else:
            if not all([args.username,args.full_name,args.department_id,args.role]):
                raise ValidationError('Yangi xodim uchun ism, login, bo‘linma va rolni aniqlashtiring.')
            before = 'Yangi hisob'
            temporary = secrets.token_urlsafe(40)
            item = validate(EmployeeForm({'username':args.username,'full_name':args.full_name,
                'job_title':args.job_title or '', 'department':args.department_id,'role':args.role,
                'password1':temporary,'password2':temporary})).save()
            # No password ever enters the LLM, proposal, response or chat history.
            item.set_unusable_password()
        if args.is_active is not None:
            item.is_active=args.is_active
        item.save()
        preview={'Amal':'Xodimni tahrirlash' if args.employee_id else 'Xodim yaratish','Oldingi holat':before,
                 'Xodim':item.full_name,'Login':item.username,'Lavozim':item.job_title,'Bo‘linma':str(item.department),
                 'Rol':item.get_role_display(),'Faol':'Ha' if item.is_active else 'Yo‘q'}
        if not args.employee_id:preview['Parol']='Keyingi xavfsiz sahifada belgilanadi; hozir hisobga kirib bo‘lmaydi.'
        url=reverse('structure') if args.employee_id else reverse('employee_password',kwargs={'pk':item.pk})
        return preview, {'message':item.full_name+' — ma’lumotlar saqlandi.'+(' Kirish uchun ochilgan sahifada parol belgilang.' if not args.employee_id else ''), 'navigation':{'url':url,'label':'Xodim'}}
    if name == 'prepare_task_edit':
        task=Task.objects.select_for_update().filter(pk=args.task_id,pk__in=Task.objects.visible_to(user)).first()
        require(task is not None and can_manage(user,task))
        if task.status != 'active':raise ValidationError('Faqat jarayondagi topshiriq mazmuni va ijrochisi o‘zgartiriladi.')
        before=f'{task.title}; {task.description}; {task.assignee.full_name}'
        old_assignee=task.assignee
        if args.assignee_id is not None and args.assignee_id != task.assignee_id:
            target=assignees_for(user).filter(pk=args.assignee_id).first()
            require(target is not None)
            if task.children.exists():raise ValidationError('Quyi taqsimotlari bor topshiriqning ijrochisini almashtirish mumkin emas.')
            task.assignee=target;task.seen_at=None
        if args.title is not None:task.title=args.title.strip()
        if args.description is not None:task.description=args.description
        task.full_clean();task.save()
        log(task,user,Event.Kind.COMMENT,'Topshiriq tahrirlandi: '+before+' → '+task.title+'; '+task.assignee.full_name)
        notify(task,[old_assignee,task.assignee], 'Topshiriq ma’lumotlari o‘zgartirildi')
        return {'Amal':'Topshiriqni tahrirlash','Oldingi holat':before,'Mazmun':task.title,'Talablar':task.description,'Ijrochi':task.assignee.full_name}, {'message':task.code+' — o‘zgarishlar saqlandi.','task_id':task.pk,'navigation':{'url':task.get_absolute_url(),'label':task.code}}
    if name == 'prepare_participant':
        participant = add_participant(user, args.task_id, args.person_id, args.part, args.kind)
        label = participant.get_kind_display()
        return ({'Amal':label+' qo‘shish','Topshiriq':participant.task.code+' — '+participant.task.title,
                 'Xodim':participant.user.full_name,'Ijro qismi':participant.part or 'Umumiy nazorat'},
                {'message':f'{participant.user.full_name} — {label.lower()} qilib qo‘shildi.','task_id':participant.task_id,
                 'navigation':{'url':participant.task.get_absolute_url(),'label':participant.task.code}})
    if name == 'prepare_part_action':
        participant = TaskParticipant.objects.select_related('task','user').get(pk=args.participant_id)
        before = participant.get_status_display()
        part_action(user, args.participant_id, args.action, args.text)
        labels = {'submit_part':'Qismni topshirish','accept_part':'Qismni qabul qilish',
                  'return_part':'Qismni qaytarish','remove':'Ishtirokchini olib tashlash'}
        return ({'Amal':labels[args.action],'Topshiriq':participant.task.code+' — '+participant.task.title,
                 'Xodim':participant.user.full_name,'Ijro qismi':participant.part or 'Umumiy nazorat',
                 'Oldingi holat':before,'Matn':args.text},
                {'message':labels[args.action]+' bajarildi. '+participant.task.code,'task_id':participant.task_id,
                 'navigation':{'url':participant.task.get_absolute_url(),'label':participant.task.code}})
    items=user.notifications.filter(read_at__isnull=True,task__in=Task.objects.visible_to(user))
    count=items.update(read_at=timezone.now())
    return {'Amal':'Xabarnomalarni o‘qilgan deb belgilash','Soni':str(count)}, {'message':f'{count} ta xabarnoma o‘qilgan deb belgilandi.','navigation':{'url':reverse('notifications'),'label':'Xabarnomalar'}}


def prepare(user, conversation, name, args):
    stamp=version(user,name,args)
    with transaction.atomic():
        preview,_=apply(user,name,args)
        transaction.set_rollback(True)
    AgentProposal.objects.filter(conversation=conversation,state='pending').update(state='cancelled')
    proposal=AgentProposal.objects.create(conversation=conversation,payload={'tool':name,'args':args.model_dump()},
        preview=preview,snapshot=stamp,expires_at=timezone.now()+timedelta(minutes=10))
    from .agent_tools import proposal_data
    return {'proposal':proposal_data(proposal),'message':'O‘zgarish tayyor. Tekshirib, «tasdiqlayman» deng yoki tasdiqlash tugmasini bosing.'}


def confirm(user, proposal, name, args):
    # Called inside the proposal transaction; recheck permissions and current data.
    if name == 'prepare_employee' and args.employee_id:
        User.objects.select_for_update().filter(pk=args.employee_id).first()
    elif name == 'prepare_department' and args.department_id:
        Department.objects.select_for_update().filter(pk=args.department_id).first()
    elif name == 'prepare_task_edit':
        Task.objects.select_for_update().filter(pk=args.task_id).first()
    elif name == 'prepare_participant':
        Task.objects.select_for_update().filter(pk=args.task_id).first()
    elif name == 'prepare_part_action':
        TaskParticipant.objects.select_for_update().filter(pk=args.participant_id).first()
    if version(user,name,args) != proposal.snapshot:
        raise ValidationError('Ma’lumot o‘zgargan. O‘zgarishni yangidan tayyorlang.')
    _,result=apply(user,name,args)
    result['proposal']=None
    proposal.state,proposal.result='completed',result
    proposal.save(update_fields=['state','result'])
    return result
