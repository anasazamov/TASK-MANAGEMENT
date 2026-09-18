import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from django.core.asgi import get_asgi_application

django_application = get_asgi_application()
from core.live import relay


async def application(scope, receive, send):
    if scope['type'] == 'websocket':
        await relay(scope, receive, send)
    else:
        await django_application(scope, receive, send)
