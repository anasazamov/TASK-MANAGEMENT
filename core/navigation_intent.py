"""Bounded spoken page commands. Never infer mutations or missing person names."""
import re

from .person_search import words

OPEN_WORDS = {
    'och', 'ochib', 'oching', 'ochgin', 'ochavering', 'ochvor', 'ochvoring', 'ochber',
    'ochibber', 'ochilsin', 'ochiladi', 'ochiling', 'ochiladigan',
    'kir', 'kiring', 'kirgin', 'kiriladi', 'kirildi', 'ot', 'oting', 'oling',
    'open', 'show',
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


def page_target(command):
    if negative(command) or writes(command):
        return None
    tokens = [t for t in words(command) if t not in FILLER_WORDS and not open_word(t)]
    text = ' '.join(tokens)
    patterns = {
        'dashboard': r'(?:(?:boshqaruv|boshqaru|boshqaroq) panel(?:i|ini|iga)?(?: sahifa(?:si|sini|ga))?|(?:bosh|asosiy) sahifa(?:ni|si|sini|ga)?|mening panelim)',
        'employees': r'xodimlar(?:ni|ga)?(?: sahifa(?:si|sini|ga))?',
        'structure': r'struktura(?:ni|ga)?(?: sahifa(?:si|sini|ga))?',
        'timeline': r'harakatlar tarixi(?:ni|ga)?(?: sahifa(?:si|sini|ga))?',
        'notifications': r'xabarnomalar(?:ni|ga)?(?: sahifa(?:si|sini|ga))?',
        'chains': r'nazorat zanjiri(?:ni|ga)?(?: sahifa(?:si|sini|ga))?',
    }
    return next((page for page, pattern in patterns.items() if re.fullmatch(pattern, text)), None)


def navigation(command):
    return not negative(command) and (page_target(command) is not None or any(open_word(t) for t in words(command)))


def read_only(command):
    tokens = words(command)
    category = any(t.startswith(('muddatsiz', 'kechikkan')) for t in tokens) or ('tasdiq' in tokens and any(t.startswith('kutil') for t in tokens))
    task_list = category and any(t.startswith(('topshiriqlar', 'vazifalar')) for t in tokens)
    return not negative(command) and not writes(command) and (navigation(command) or task_list)
