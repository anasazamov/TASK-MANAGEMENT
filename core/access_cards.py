"""Map the persons enrolled on the door station to our users.

The device spells names its own way, upper-case and without apostrophes, with
the given name first ("ILYOS AXMEDOV" for "Ahmedov Ilyos Izzatullo o‘g‘li"),
and the spelling of vowels and x/h drifts. So the match is fuzzy, per name part,
and only a clear, unique best match is taken; the rest is shown for a human to
map by hand.
"""
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from django.db import transaction

from .models import AccessCard, User

APOSTROPHES = re.compile('[’‘ʻʼ\'`´]')
PATRONYMIC_TAILS = {'ogli', 'qizi'}
SURE = 0.85
LIKELY = 0.75
MARGIN = 0.1


def _part(token):
    token = APOSTROPHES.sub('', token.casefold())
    return token.replace('x', 'h').replace('iy', 'i').replace('ye', 'e')


def name_parts(name):
    return [_part(token) for token in name.split()
            if APOSTROPHES.sub('', token.casefold()) not in PATRONYMIC_TAILS]


def _skeleton(part):
    return part[:1] + re.sub('[aeiouy]', '', part[1:])


def _part_score(a, b):
    if a == b:
        return 1.0
    # Aziz/Azizbek, Hamidullo/Hamidulla: one name is a short form of the other.
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        return 0.9
    return max(SequenceMatcher(None, a, b).ratio(),
               SequenceMatcher(None, _skeleton(a), _skeleton(b)).ratio() * 0.95)


def name_score(device_name, full_name):
    """0..1: how well every part of the device name is found in the full name."""
    device, ours = name_parts(device_name), name_parts(full_name)
    if not device or not ours:
        return 0.0
    best = [max(_part_score(part, other) for other in ours) for part in device]
    score = sum(best) / len(best)
    # A user entered by surname alone: a strong surname hit is a likely match.
    if len(ours) == 1 and max(best) >= 0.9:
        score = max(score, 0.8)
    return score


@dataclass
class Row:
    employee_no: str
    name: str
    cards: list
    user: User = None
    score: float = 0.0
    state: str = ''          # sure, likely, manual, unmatched, skipped
    runner_up: User = None
    outcome: str = ''        # created, updated, unchanged, conflict
    notes: list = field(default_factory=list)


def match(people, users, manual=None, skip=()):
    """Pair each device person with at most one user, best scores first."""
    manual = manual or {}
    rows = [Row(str(p['employee_no']), p.get('name', ''), [str(c) for c in p.get('cards', []) if c])
            for p in people]
    taken = set()
    for row in rows:
        if row.employee_no in skip:
            row.state = 'skipped'
        elif row.employee_no in manual:
            row.user, row.score, row.state = manual[row.employee_no], 1.0, 'manual'
            taken.add(row.user.pk)
    scored = []
    for row in rows:
        if row.state:
            continue
        ranked = sorted(((name_score(row.name, user.full_name or user.username), user) for user in users),
                        key=lambda pair: pair[0], reverse=True)
        scored.append((ranked[0][0] if ranked else 0.0, row, ranked))
    for _top, row, ranked in sorted(scored, key=lambda item: item[0], reverse=True):
        free = [(score, user) for score, user in ranked if user.pk not in taken]
        if not free or free[0][0] < LIKELY:
            row.state = 'unmatched'
            if free:
                row.score, row.runner_up = free[0][0], free[0][1]
            continue
        score, user = free[0]
        second = free[1][0] if len(free) > 1 else 0.0
        row.user, row.score = user, score
        row.runner_up = free[1][1] if len(free) > 1 else None
        row.state = 'sure' if score >= SURE and score - second >= MARGIN else 'likely'
        taken.add(user.pk)
    for row in rows:
        row.state = row.state or 'unmatched'
    return rows


def _save(row):
    """Record one person's employee number and cards against the matched user."""
    by_employee = AccessCard.objects.filter(device_employee_no=row.employee_no).first()
    owners = {card.card_no: card for card in AccessCard.objects.filter(card_no__in=row.cards)}
    clash = [c for c in [by_employee, *owners.values()] if c and c.user_id != row.user.pk]
    if clash and row.state != 'manual':
        row.outcome = 'conflict'
        row.notes = [f'{c.card_no or c.device_employee_no} allaqachon {c.user.full_name}ga bog‘langan' for c in clash]
        return
    primary = by_employee or (owners.get(row.cards[0]) if row.cards else None) or AccessCard(user=row.user)
    created = primary.pk is None
    before = (primary.user_id, primary.card_no, primary.device_employee_no, primary.label, primary.active)
    first_card = row.cards[0] if row.cards else primary.card_no
    stale = owners.get(first_card)
    if stale and stale.pk != primary.pk:
        stale.delete()      # the same card on a second row; it moves onto the primary one
    primary.user, primary.device_employee_no, primary.card_no = row.user, row.employee_no, first_card
    primary.label, primary.active = row.name[:180], True
    primary.save()
    changed = before != (primary.user_id, primary.card_no, primary.device_employee_no, primary.label, primary.active)
    for card_no in row.cards[1:]:
        extra = owners.get(card_no) or AccessCard(card_no=card_no)
        if extra.pk is None or extra.user_id != row.user.pk or not extra.active:
            changed = True
        extra.user, extra.label, extra.active = row.user, row.name[:180], True
        extra.save()
    row.outcome = 'created' if created else ('updated' if changed else 'unchanged')


def apply(rows, include_likely=False):
    """Write the matched rows; `likely` ones only when asked. All or nothing."""
    with transaction.atomic():
        for row in rows:
            if row.state in ('sure', 'manual') or (row.state == 'likely' and include_likely):
                _save(row)
    return rows
