"""Prepare a spoken copy of replies; never rewrite the displayed transcript."""
import re
from datetime import date


ONES = ('nol', 'bir', 'ikki', 'uch', 'to‘rt', 'besh', 'olti', 'yetti', 'sakkiz', 'to‘qqiz')
TENS = ('', 'o‘n', 'yigirma', 'o‘ttiz', 'qirq', 'ellik', 'oltmish', 'yetmish', 'sakson', 'to‘qson')


def number(value):
    if value < 10:
        return ONES[value]
    for scale, label in ((1000000000, 'milliard'), (1000000, 'million'), (1000, 'ming'), (100, 'yuz'), (10, '')):
        if value >= scale:
            first, rest = divmod(value, scale)
            prefix = TENS[first] if scale == 10 else ((number(first)+' ') if first != 1 or scale >= 1000000 else '')+label
            return prefix + (' '+number(rest) if rest else '')


MONTHS = ('', 'yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust', 'sentabr', 'oktabr', 'noyabr', 'dekabr')
ACRONYMS = {'IT': 'ay ti', 'ERP': 'i ar pi', 'CRM': 'si ar em', 'KPI': 'key pi ay', 'API': 'ey pi ay'}


def ordinal(value):
    word = number(value)
    return word + ('nchi' if word[-1] in 'aeiou' else 'inchi')


def spoken_date(match):
    day, month, year = map(int, match.groups())
    try:
        date(year, month, day)
    except ValueError:
        return match[0]
    return f'{ordinal(year)} yil, {ordinal(day)} {MONTHS[month]}'


def spoken_text(text):
    text = re.sub(r'\[([^\]]+)\]\([^\s)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'(?m)^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)', '', text)
    text = text.replace('**', '').replace('__', '').replace('`', '')
    text = re.sub(r'\bT\s*[-–]?\s*(\d{3,9})(ni|ning|ga)?\b',
        lambda m: number(int(m[1]))+' raqamli topshiriq'+(m[2] or ''), text, flags=re.I)
    text = re.sub(r'\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b', spoken_date, text)
    text = re.sub(r'(?:soat\s+)?\b([01]?\d|2[0-3]):([0-5]\d)(?:\s*(gacha|dan|da))?\b',
        lambda m: 'soat '+number(int(m[1]))+(' '+number(int(m[2])) if int(m[2]) else '')+(m[3] or ''), text, flags=re.I)
    text = re.sub(r'\b(?:IT|ERP|CRM|KPI|API)\b', lambda m: ACRONYMS[m[0]], text)
    # Only expand known count contexts; preserve decimals, IDs and unknown codes.
    text = re.sub(r'(?<![\w.,-])(\d{1,9})(?=\s+(?:ta|kun|soat|daqiqa|foiz|xodim|topshiriq)\b)',
        lambda m: number(int(m[1])), text)
    text = re.sub(r'\b(\d{1,6})-sahifa\b', lambda m: ordinal(int(m[1]))+' sahifa', text)
    # A list row without punctuation must not run into the next employee/task.
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    text = ' '.join(row + ('.' if i < len(rows)-1 and row[-1] not in '.!?:;' else '') for i, row in enumerate(rows))
    text = text.translate(str.maketrans({c: "'" for c in '‘’ʻʼ'}))
    return re.sub(r'\s+', ' ', text).strip()
