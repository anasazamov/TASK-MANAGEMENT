"""Server-written replies from this turn's verified tool results.

The model chooses a source and a focus, never the factual wording. A source ID
is local to one turn; old chat text cannot act as evidence.
"""
from datetime import datetime
from typing import Literal

from django.core.exceptions import ValidationError
from django.utils import timezone
from pydantic import Field

from .agent_tools import Arguments


class Answer(Arguments):
    source: str = Field(min_length=1, max_length=12)
    focus: Literal['overview', 'count', 'assignee', 'deadline', 'status', 'description', 'history', 'children', 'actions', 'top', 'bottom'] = Field(
        description='Use overview when several facts are requested: it includes assignee, deadline, title and status. Use a specific focus only for a single question. '
                    'For a dyn_ tool result, top/bottom answer "eng ko‘p / kim ko‘p / eng kam" by the FIRST numeric column; the server reports ties.')


class Clarify(Arguments):
    kind: Literal['person', 'task', 'work', 'deadline', 'date', 'intent', 'help', 'greeting', 'thanks', 'correction']


QUESTIONS = {
    'person': 'Qaysi xodim? Ismi yoki familiyasini ayting.',
    'task': 'Qaysi topshiriq? Uning kodi yoki mazmunini ayting.',
    'work': 'Qanday ish bajarilishi kerak?',
    'deadline': 'Qachongacha bajarilishi kerak? Sana va vaqtni ayting yoki «muddatsiz» deng.',
    'date': 'Sana yoki vaqt aniq emas. Muddatni kun, oy va soat bilan ayting.',
    'intent': 'Buyruqni aniq tushunmadim. Qaysi xodim yoki topshiriq bilan nima qilish kerak?',
    'help': 'Xodim va topshiriqlarni qidirish, sahifalarni ochish, holat va muddatlarni tekshirishga yordam beraman. O‘zgartirishlar avval tasdiqlash uchun ko‘rsatiladi.',
    'greeting': 'Assalomu alaykum. Qaysi topshiriq bo‘yicha yordam kerak?',
    'thanks': 'Marhamat. Yana qaysi topshiriq bo‘yicha yordam kerak?',
    'correction': 'Ma’lumotni bazadan qayta tekshiraman. Qaysi xodim yoki topshiriq nazarda tutilgan?',
}

TOOLS = {
    'answer_from_source': (Answer, 'Answer using one source_id returned by a read tool IN THIS TURN. Select a focus; the server writes all facts. Never supply names, counts or prose. To open something use navigate instead.'),
    'ask_clarification': (Clarify, 'Ask one standard question about missing information or respond to greeting/thanks/help. No factual business claims. For employee choices call list_people; the server presents real candidates.'),
}


def schemas():
    return [{'type': 'function', 'name': name, 'description': description, 'strict': True,
             'parameters': model.model_json_schema()} for name, (model, description) in TOOLS.items()]


STATES = {'active': 'Jarayonda', 'overdue': 'Muddati o‘tgan', 'soon': 'Muddati yaqin',
          'submitted': 'Tasdiq kutilmoqda', 'accepted': 'Qabul qilingan', 'undated': 'Muddatsiz'}


def date(value):
    return timezone.localtime(datetime.fromisoformat(value)).strftime('%d.%m.%Y soat %H:%M') if value else 'Muddatsiz'


def person_row(person):
    details = ', '.join(filter(None, [person['job_title'], person['department']]))
    return person['name'] + (' — '+details if details else '')


def task_row(task):
    return f"{task['code']}: {task['title']} — {task['assignee']}. {STATES[task['status']]}. Muddat: {date(task['due_at'])}."


def lines(items):
    return '\n'.join(f'{i}. {item}' for i, item in enumerate(items, 1))


def cell(item):
    if isinstance(item, float):
        return f'{item:.2f}'
    return '—' if item is None else str(item)


def describe(row):
    return '; '.join(k+': '+cell(v) for k, v in row.items())


def extreme(rows, highest):
    numeric = [k for k in (rows[0] if rows else {})
               if all(type(r[k]) in (int, float) for r in rows)]
    if not numeric:
        return 'Natijada taqqoslanadigan son ustuni yo‘q.'
    key = numeric[0]
    best = (max if highest else min)(r[key] for r in rows)
    winners = [r for r in rows if r[key] == best]
    label = ('Eng ko‘p' if highest else 'Eng kam')+f' {key}: {cell(best)}.'
    if len(winners) == 1:
        return label+' '+describe(winners[0])
    if len(winners) == len(rows):
        label += f' Barcha {len(rows)} ta qatorda qiymat teng, bittasini ajratib bo‘lmaydi.'
    else:
        label += f' Bu qiymat {len(winners)} ta qatorda teng.'
    shown = lines([describe(r) for r in winners[:40]])
    return label+'\n'+shown+('\n…' if len(winners) > 40 else '')


def render(name, data, focus='overview'):
    if name == 'create_tool':
        return data['message']
    if name == 'list_pages':
        if not data['pages']:
            return 'Hozircha saqlangan sahifa yo‘q. Kerakli ma’lumotni ayting, sahifa tayyorlab beraman.'
        return 'Saqlangan sahifalar:\n'+lines([f"{p['title']} (#{p['id']}, {p['updated_at']})" for p in data['pages']])
    if name.startswith('dyn_'):
        result = data['data']
        if 'rows' not in result:
            return render(data['result_kind'], result, focus)
        if focus in ('top', 'bottom'):
            return extreme(data.get('all_rows') or result['rows'], focus == 'top')
        message = f"Natija: {result['total']} ta qator."
        if result.get('truncated'):
            message += ' Quyida dastlabki qismi ko‘rsatilgan.'
        if focus == 'count':
            return message
        return message+'\n'+lines([describe(row) for row in result['rows']])
    if name == 'read_structure':
        departments = {item['id']: item['name'] for item in data['departments']}
        return 'Bo‘linmalar:\n'+lines(departments.values())+'\nXodimlar:\n'+lines([
            p['full_name']+' — '+departments.get(p['department_id'], 'Bo‘linmasiz')+
            '; '+p['role']+'; '+('faol' if p['is_active'] else 'bloklangan') for p in data['employees']])
    if name == 'list_people':
        if not data['people']:
            return 'Bu qidiruv bo‘yicha sizga ko‘rinadigan xodim topilmadi. Ism yoki familiyani aniqlashtiring.'
        entries = lines([person_row(p) for p in data['people']])
        if data['needs_clarification']:
            prefix = 'Yozilishi yaqin xodimlar' if data['match'] == 'suggested' else 'Bir nechta xodim mos keldi'
            return prefix+':\n'+entries+'\nQaysi xodimni nazarda tutdingiz? To‘liq ismini ayting.'
        return f"Qidiruv bo‘yicha {data['total']} ta xodim topildi"+(' (dastlabki 40 tasi)' if data['truncated'] else '')+':\n'+entries
    if name == 'search_tasks':
        message = f"Sizga ko‘rinadigan topshiriqlardan shu qidiruv va filtr bo‘yicha {data['total']} ta topildi."
        if focus == 'count' or not data['tasks']:
            return message
        return message+f" {data['page']}-sahifa:\n"+lines([task_row(t) for t in data['tasks']])
    if name == 'get_task':
        title = data['code']+' — '+data['title']+'.'
        if focus == 'assignee':
            text = title+' Ijrochi: '+data['assignee']+'. Topshiriq bergan: '+data['issuer']+'.'
            if data.get('participants'):
                text += ' Qo‘shimcha ijrochilar:\n'+lines([f"{p['name']} — {p['part']} ({p['status']})" for p in data['participants']])
            return text
        if focus == 'deadline':
            return title+' Muddat: '+date(data['due_at'])+' (Toshkent vaqti).'
        if focus == 'status':
            return title+' Holati: '+STATES[data['status']]+'.'
        if focus == 'description':
            return title+' Topshiriq matni: '+(data['description'] or 'Qo‘shimcha talablar yozilmagan.')
        if focus == 'history':
            return title+' So‘nggi harakatlar:\n'+(lines([f"{date(e['time'])} — {e['kind']}. Yozuv: {e['text']}" for e in data['history']]) or 'Harakatlar qayd etilmagan.')
        if focus == 'children':
            return title+' Ko‘rinadigan quyi topshiriqlar:\n'+(lines([task_row(t) for t in data['children']]) or 'Quyi topshiriq topilmadi.')
        if focus == 'actions':
            from .agent_tools import ACTION_LABELS
            return title+' Mumkin bo‘lgan amallar: '+', '.join(ACTION_LABELS[a] for a in data['permitted_actions'])+'.'
        return task_row(data)
    if name == 'get_summary':
        return 'Sizga ko‘rinadigan topshiriqlar holati:\n'+lines([f"{STATES[key]}: {value} ta" for key, value in data['counts'].items()])
    if name == 'get_notifications':
        return 'So‘nggi xabarnomalar:\n'+(lines([f"{n['code']}: {n['text']}" for n in data['notifications']]) or 'Xabarnoma topilmadi.')
    if name == 'recent_activity':
        return 'Ko‘rinadigan so‘nggi harakatlar:\n'+(lines([f"{date(e['time'])}, {e['code']}, {e['actor']}: {e['kind']}. Yozuv: {e['text']}" for e in data['events']]) or 'Tanlangan davrda harakat topilmadi.')
    raise ValidationError('Javob uchun tekshirilgan ma’lumot topilmadi.')


def execute(name, raw, sources):
    args = TOOLS[name][0].model_validate(raw)
    if name == 'ask_clarification':
        return QUESTIONS[args.kind]
    if args.source not in sources:
        raise ValidationError('Bu javob manbasi tekshirilmagan. Ma’lumotni qayta qidiring.')
    name, data = sources[args.source]
    return render(name, data, args.focus)
