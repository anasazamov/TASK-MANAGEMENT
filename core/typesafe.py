"""Typed judgments from TypeSafe's Jev, used where code needs common sense.

The model writes no text and performs no action here. It answers one narrow
question — is this speech addressed to us, which of these employees is meant —
and this module returns None whenever the answer is missing, slow, uncertain or
the service is unreachable. Every caller then behaves exactly as it did before
TypeSafe existed, so an outage costs a clarification question, never a feature.
"""
import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)
URL = 'https://api.typesafe.ai/v1/systemone'
# Short: both callers sit in a path a person is waiting on, and both can proceed
# without an answer. Waiting longer than this is worse than not asking.
TIMEOUT = httpx.Timeout(6, connect=3)


def configured():
    return bool(settings.TYPESAFE_API_KEY)


def payload(state, questions):
    return {'model': settings.TYPESAFE_MODEL, 'state': state, 'questions': questions}


def headers():
    return {'Authorization': f'Bearer {settings.TYPESAFE_API_KEY}'}


def answers(response):
    if response.status_code != 200:
        logger.warning('TypeSafe refused the judgment status=%s', response.status_code)
        return None
    data = response.json()
    return data.get('answers') if isinstance(data, dict) else None


def judge(state, questions):
    if not configured():
        return None
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            return answers(client.post(URL, headers=headers(), json=payload(state, questions)))
    except (httpx.RequestError, ValueError) as error:
        logger.warning('TypeSafe judgment failed: %s', type(error).__name__)
        return None


async def judge_async(state, questions):
    if not configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return answers(await client.post(URL, headers=headers(), json=payload(state, questions)))
    except (httpx.RequestError, ValueError) as error:
        logger.warning('TypeSafe judgment failed: %s', type(error).__name__)
        return None


COMMAND_QUESTION = {'addressed': {'type': 'noul', 'instructions':
    'Quyidagi gap topshiriqlarni boshqarish tizimiga aytilgan buyruq yoki savolmi? '
    'Topshiriq, ijrochi, muddat, hisobot, xodim yoki sahifa haqidagi murojaat — ha. '
    'Yonidagi odamlar bilan suhbat, telefon gaplashuvi yoki tizimga aloqasi yo‘q gap — yo‘q.'}}


async def addressed_to_us(text):
    """True when this utterance is meant for the assistant.

    The microphone stays open through a whole conversation, so speech in the
    room reaches the transcriber too. An unanswered question means yes: being
    deaf is worse than acting on one stray sentence, which still has to pass
    the usual confirmation before anything changes.
    """
    if not configured() or not text:
        return True
    result = await judge_async({'utterance': text}, COMMAND_QUESTION)
    value = (result or {}).get('addressed', {}).get('noul')
    if not isinstance(value, (int, float)):
        return True
    if value < settings.TYPESAFE_COMMAND_MIN:
        logger.info('Utterance treated as room speech probability=%.2f', value)
        return False
    return True


def choose_person(command, speaker, candidates):
    """The employee this command means, or None to let the person say which.

    Only used where the code already found several equally valid matches and
    would otherwise ask. A confident answer saves that round trip; anything
    less keeps the question.
    """
    if not configured() or len(candidates) < 2 or len(candidates) > 12 or not command:
        return None
    options = {str(person.pk): f'{person.full_name}, {person.job_title}'
               + (f', {person.department.name} bo‘limi' if person.department else '')
               for person in candidates}
    state = {'command': command[:500],
             'speaker': {'name': speaker.full_name, 'role': speaker.get_role_display(),
                         'department': speaker.department.name if speaker.department else ''},
             'candidates': [{'id': str(p.pk), 'name': p.full_name, 'job_title': p.job_title,
                             'department': p.department.name if p.department else ''} for p in candidates]}
    result = judge(state, {'person': {'type': 'choice',
        'instructions': 'Buyruqda qaysi xodim nazarda tutilgan? Bir xil ismli xodimlar orasidan '
                        'gapning mazmuni, lavozimi va so‘rovchining bo‘limi bo‘yicha tanlang.',
        'criteria': {**options, 'none': 'Aniq emas — foydalanuvchidan so‘rash kerak'}}})
    answer = (result or {}).get('person', {})
    chosen = answer.get('choice')
    probability = (answer.get('probabilities') or {}).get(chosen, 0)
    if chosen in options and probability >= settings.TYPESAFE_PERSON_MIN:
        return int(chosen)
    return None
