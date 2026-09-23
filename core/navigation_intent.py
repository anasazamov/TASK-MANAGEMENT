"""Bounded spoken page commands. Never infer mutations or missing person names."""
import re

from .person_search import words

OPEN_WORDS = {
    'och', 'ochib', 'oching', 'ochgin', 'ochavering', 'ochvor', 'ochvoring', 'ochber',
    'ochibber', 'ochilsin', 'ochiladi', 'ochiling', 'ochiladigan',
    'kir', 'kiring', 'kirgin', 'kiriladi', 'kirildi', 'ot', 'oting', 'oling',
    'open', 'show', 'ot', 'otamiz', 'oting', 'otish', 'otaylik', 'otsak',
}
FILLER_WORDS = {'iltimos', 'menga', 'endi', 'bu', 'yerdan', 'yerda', 'shu', 'ham', 'ber', 'bering', 'olib', 'boladi'}


def negative(command):
    return any(t.startswith(('ochma', 'ochilma', 'korsatma', 'kirma', 'kirilma')) for t in words(command))


def open_word(token):
    return token in OPEN_WORDS or (token.startswith(('korsat', 'chiqar', 'otkroy', 'pokaji', 'pereydi')) and not token.startswith('korsatkich'))


def writes(command):
    return any(t == 'yangi' or (t.startswith(('yarat', 'ozgartir', 'uzaytir', 'tayinla', 'taqsimla',
        'belgila', 'tasdiqla', 'yubor', 'tugat', 'bajarsin', 'almashtir', 'bekor', 'qosh',
        'tahrirla', 'blokla', 'faollashtir')))
        for t in words(command))


# A page is named in many ways: the page itself, its section, its list, its
# statistics, and any of those with a case ending. Every spelling that clearly
# means one page belongs here, because whatever falls through is left to the
# model, which may answer a page request with a task tool instead.
PLACE = r'(?:\s+(?:sahifa|bolim|bolimi|royxat|royxati|ruyxat|ruyxati|statistika|statistikasi|jadval|jadvali)(?:si|sini|siga|sidan|sida|ga|ni|da|dan|dagi|lari|larini)?)*'
ENDING = r'(?:ni|ning|ga|da|dan|i|ini|iga|ida|idan|si|sini|siga)?'


def page_pattern(*names):
    return r'(?:' + '|'.join(names) + r')' + ENDING + PLACE


def page_target(command):
    if negative(command) or writes(command):
        return None
    tokens = [t for t in words(command) if t not in FILLER_WORDS and not open_word(t)]
    text = ' '.join(tokens)
    patterns = {
        'dashboard': r'(?:(?:boshqaruv|boshqaru|boshqaroq) panel' + ENDING + PLACE
                     + r'|(?:bosh|asosiy) sahifa' + ENDING + r'|mening panelim)',
        'employees': page_pattern('xodimlar', 'hodimlar', 'ishchilar'),
        'structure': page_pattern('struktura', 'tuzilma'),
        'timeline': r'harakatlar tarix' + ENDING + PLACE + r'|tarix' + ENDING + PLACE,
        'notifications': page_pattern('xabarnomalar', 'bildirishnomalar'),
        'chains': r'nazorat zanjir' + ENDING + PLACE,
        'generated_pages': page_pattern('agent sahifalari', 'agent sahifalar'),
    }
    return next((page for page, pattern in patterns.items() if re.fullmatch(pattern, text)), None)


def navigation(command):
    return not negative(command) and (page_target(command) is not None or any(open_word(t) for t in words(command)))


def read_only(command):
    tokens = words(command)
    category = any(t.startswith(('muddatsiz', 'kechikkan')) for t in tokens) or ('tasdiq' in tokens and any(t.startswith('kutil') for t in tokens))
    task_list = category and any(t.startswith(('topshiriqlar', 'vazifalar')) for t in tokens)
    return not negative(command) and not writes(command) and (navigation(command) or task_list)
