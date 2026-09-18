import hashlib
import json
import uuid
from datetime import timedelta

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import agent, agent_tools, voice, voicelab
from .models import AgentConversation, AgentProposal, AgentTurn
from .speech_text import spoken_text
from .voice_errors import VoiceError
from .voice_views import json_payload, rate_limit, voice_access


def identifier(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise VoiceError('invalid_id', 'Suhbat identifikatori noto‘g‘ri. Yangi suhbat boshlang.')


def conversation_for(user, value):
    conversation, _ = AgentConversation.objects.get_or_create(pk=identifier(value), defaults={'user': user})
    if conversation.user_id != user.pk:
        raise VoiceError('forbidden', 'Bu suhbat sizga tegishli emas.', 403)
    people = list(agent_tools.people(user).order_by('pk').values_list('pk', flat=True))
    people_scope = hashlib.sha256(json.dumps(people).encode()).hexdigest()[:16]
    scope = f'{user.role}:{user.department_id}:{user.is_active}:{people_scope}'
    allowed = set(agent_tools.visible(user).filter(pk__in=conversation.references).values_list('pk', flat=True))
    if conversation.scope != scope or allowed != set(conversation.references) or conversation.updated_at < timezone.now()-timedelta(days=1):
        # Do not return old transcripts or feed them back to the model after a
        # role/department/assignment change revokes access to referenced data.
        conversation.messages, conversation.references, conversation.scope = [], [], scope
        conversation.save(update_fields=['messages', 'references', 'scope', 'updated_at'])
        AgentProposal.objects.filter(conversation=conversation, state='pending').update(state='cancelled')
        AgentTurn.objects.filter(conversation=conversation).delete()
    return conversation


def current_proposal(conversation):
    pending = AgentProposal.objects.filter(conversation=conversation, state='pending', expires_at__gt=timezone.now()).first()
    return agent_tools.proposal_data(pending) if pending else None


def speech_reply(user, result):
    text = voicelab.speech_excerpt(spoken_text(result['message']))
    result['reply_token'] = signing.dumps({'user': user.pk, 'text': text, 'id': 'tts-' + uuid.uuid4().hex}, salt='voice-tts')
    return result


@voice_access
@require_POST
def state(request):
    conversation = conversation_for(request.user, json_payload(request).get('conversation_id'))
    last = AgentTurn.objects.filter(conversation=conversation, response__isnull=False).order_by('-created_at').first()
    token = last.response.get('reply_token') if last and last.response.get('grounded') else None
    if token:
        try:
            signing.loads(token, salt='voice-tts', max_age=900)
        except signing.BadSignature:
            token = None
    return JsonResponse({'conversation_id': str(conversation.pk), 'messages': conversation.messages,
                         'proposal': current_proposal(conversation),
                         'reply_token': token,
                         'busy': bool(conversation.busy_until and conversation.busy_until > timezone.now())})


@voice_access
@require_POST
def message(request):
    rate_limit(request)
    data = json_payload(request)
    command, path = data.get('command'), data.get('path', '')
    if not isinstance(command, str) or not command.strip() or len(command) > 6000:
        raise VoiceError('invalid_command', '1–6000 belgidan iborat buyruq yuboring.')
    if not isinstance(path, str) or len(path) > 300:
        raise VoiceError('invalid_context', 'Sahifa manzili noto‘g‘ri.')
    request_id = identifier(data.get('request_id'))
    conversation = conversation_for(request.user, data.get('conversation_id'))
    fingerprint = hashlib.sha256(json.dumps([command, path, data.get('proposal_id')], ensure_ascii=False).encode()).hexdigest()
    old = AgentTurn.objects.filter(pk=request_id).first()
    if old:
        if old.conversation_id != conversation.pk or old.fingerprint != fingerprint:
            raise VoiceError('request_conflict', 'Bu so‘rov identifikatori avval ishlatilgan.', 409)
        if old.response:
            if not old.response.get('grounded'):
                # Do not replay unchecked prose from before this change, nor
                # repeat an old action while refreshing its wording.
                return JsonResponse({'message': 'Bu eski javob tekshirilmagan. Buyruqni yangidan yuboring.',
                    'mode': 'grounded', 'grounded': True, 'conversation_id': str(conversation.pk),
                    'messages': conversation.messages, 'proposal': current_proposal(conversation)})
            return JsonResponse(old.response, status=old.response.get('http_status', 200))
    lease = timezone.now()+timedelta(seconds=120)
    claimed = AgentConversation.objects.filter(pk=conversation.pk).filter(Q(busy_until__isnull=True) | Q(busy_until__lte=timezone.now())).update(busy_until=lease)
    if not claimed:
        raise VoiceError('busy', 'Oldingi buyruq bajarilmoqda. Javobni kuting.', 409)
    references = set(conversation.references)
    result = None
    try:
        AgentTurn.objects.get_or_create(pk=request_id, defaults={'conversation': conversation, 'fingerprint': fingerprint})
        proposal_id = data.get('proposal_id')
        if proposal_id is not None:
            normalized = agent.normalize(command)
            if normalized not in ('tasdiqlayman', 'bekor qil'):
                raise VoiceError('invalid_confirmation', 'Amalni tasdiqlang yoki bekor qiling.')
            result = agent_tools.confirm(request.user, conversation, identifier(proposal_id), cancel=normalized == 'bekor qil')
        else:
            result = agent.respond(request.user, conversation, command.strip(), path, references)
        if result.get('task_id'):
            references.add(result['task_id'])
    except VoiceError as error:
        result = {'error': error.code, 'message': error.message, 'http_status': error.status}
    except (ValidationError, PermissionDenied) as error:
        result = {'error': 'action_invalid', 'http_status': 403 if isinstance(error, PermissionDenied) else 400,
                  'message': '; '.join(error.messages) if isinstance(error, ValidationError) else 'Bu amal uchun huquqingiz yo‘q.'}
    finally:
        if result is not None:
            # Replies are now server-rendered facts, prompts, previews or errors.
            # This marker distinguishes them from legacy unchecked model prose.
            result['grounded'] = True
            result = speech_reply(request.user, result)
            assistant = {'role': 'assistant', 'content': result['message'], 'verified': True}
            if result.get('choices'):
                assistant.update(choices=result['choices'], navigation_pending=result.get('navigation_pending', False))
            if result.get('task_choices'):
                assistant.update(task_choices=result['task_choices'], decision_kind=result['decision_kind'], navigation_pending=True)
            messages = [*conversation.messages, {'role': 'user', 'content': command.strip()},
                        assistant][-20:]
            result.update(conversation_id=str(conversation.pk), messages=messages, proposal=current_proposal(conversation))
            with transaction.atomic():
                AgentConversation.objects.filter(pk=conversation.pk).update(messages=messages, references=sorted(references), updated_at=timezone.now())
                AgentTurn.objects.filter(pk=request_id).update(response=result)
        AgentConversation.objects.filter(pk=conversation.pk, busy_until=lease).update(busy_until=None)
    return JsonResponse(result, status=result.get('http_status', 200))


@voice_access
@require_POST
def reset(request):
    conversation = conversation_for(request.user, json_payload(request).get('conversation_id'))
    # Deleting a conversation while its model request is running would orphan
    # its result; keep the client attached until that request has finished.
    with transaction.atomic():
        conversation = AgentConversation.objects.select_for_update().get(pk=conversation.pk)
        if conversation.busy_until and conversation.busy_until > timezone.now():
            raise VoiceError('busy', 'Avval davom etayotgan buyruq tugashini kuting.', 409)
        conversation.delete()
    return JsonResponse({'message': 'Suhbat tozalandi.'})
