"""Send one in-app notification out through every channel the person enabled."""
from django.db import transaction

from . import push, telegram


def announce(notification):
    push.send(notification)
    if telegram.configured():
        transaction.on_commit(lambda: telegram.notify(notification))
