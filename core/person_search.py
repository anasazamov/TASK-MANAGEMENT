"""Search only a caller-supplied, permission-scoped set of employees.

Spelling normalization is deterministic. Partial names and near spellings are
suggestions, never an automatic choice of employee.
"""
import re
import unicodedata


CYRILLIC = str.maketrans(dict(zip(
    'абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ',
    ['a', 'b', 'v', 'g', 'd', 'e', 'yo', 'j', 'z', 'i', 'y', 'k', 'l',
     'm', 'n', 'o', 'p', 'r', 's', 't', 'u', 'f', 'x', 'ts', 'ch', 'sh',
     'sh', '', 'i', '', 'e', 'yu', 'ya', 'o', 'q', 'g', 'h'], strict=True)))
APOSTROPHES = str.maketrans('', '', "'‘’ʻʼ`´ʹ＇")
SUFFIXES = ('ning', 'niki', 'larga', 'larni', 'lardan', 'lar', 'dan', 'ni', 'ga', 'ka', 'qa', 'da')


def words(value):
    value = unicodedata.normalize('NFKC', value).casefold().translate(CYRILLIC).translate(APOSTROPHES)
    return re.findall(r'[^\W\d_]+', value, flags=re.UNICODE)


def variants(word):
    # Remove at most one case ending and only when a real name matches the stem.
    return {word} | {word[:-len(s)] for s in SUFFIXES if word.endswith(s) and len(word)-len(s) >= 3}


def spelling(word):
    # Uzbek spelling variants of one name: Xalimov/Halimov, Shohnazarov/Shonazarov, Ahmedov/Axmedov/Amedov.
    word = word.replace('x', 'h')
    return re.sub(r'(?<=[aeiou])h(?=[^aeiou]|$)', '', word)


def one_edit(a, b):
    if min(len(a), len(b)) < 4 or max(len(a), len(b)) < 5 or abs(len(a)-len(b)) > 1:
        return False
    if len(a) == len(b):
        changed = [i for i, (left, right) in enumerate(zip(a, b)) if left != right]
        return len(changed) == 1 or (len(changed) == 2 and changed[1] == changed[0]+1
            and a[changed[0]] == b[changed[1]] and a[changed[1]] == b[changed[0]])
    short, long = sorted((a, b), key=len)
    return any(long[:i]+long[i+1:] == short for i in range(len(long)))


def all_tokens(query, name, matches):
    # A repeated token cannot satisfy both first name and surname.
    if not query:
        return True
    return any(matches(query[0], part) and all_tokens(query[1:], name[:i]+name[i+1:], matches)
               for i, part in enumerate(name))


def find_people(candidates, query, limit=40):
    tokens = words(query)
    # Honorifics are not part of the employee's name.
    tokens = [t for t in tokens if not variants(t) & {'aka', 'opa', 'janob', 'xonim'}]
    entries = [(person, words(person.full_name)) for person in candidates]
    matches, kind = [], 'none'
    if not query.strip():
        matches, kind = [p for p, _ in entries], 'list'
    elif tokens and len(tokens) <= 8:
        same_spelling = lambda a, b: spelling(b) in {spelling(v) for v in variants(a)}
        for mode, predicate in (
            ('exact', lambda a, b: a == b),
            ('inflected', lambda a, b: b in variants(a)),
            ('spelling', same_spelling),
            ('suggested', lambda a, b: any((len(v) >= 3 and (b.startswith(v) or spelling(b).startswith(spelling(v))))
                                           or one_edit(v, b) for v in variants(a))),
        ):
            matches = [p for p, name in entries if all_tokens(tokens, name, predicate)]
            if matches:
                kind = mode
                break
        if kind in ('exact', 'inflected'):
            # Speech cannot tell Shohnazarov from Shonazarov; show both instead of picking one.
            matches += [p for p, name in entries if p not in matches and all_tokens(tokens, name, same_spelling)]
        if not matches:
            matches = [p for p, _ in entries if all(t in ' '.join(words(p.job_title+' '+
                (p.department.name if p.department else ''))) for t in tokens)]
            if matches:
                kind = 'job'
    total = len(matches)
    clarification = kind == 'suggested' or (kind != 'list' and total > 1)
    return matches[:limit], {'match': kind, 'total': total, 'truncated': total > limit,
        'needs_clarification': clarification}
