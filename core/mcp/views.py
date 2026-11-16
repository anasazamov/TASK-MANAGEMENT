"""The page where the chair connects Claude: issue, see and revoke personal tokens."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from ..models import McpToken
from .server import may_connect, new_token

MAX_TOKENS = 5


@login_required
@never_cache
def connect(request):
    if not settings.MCP_ENABLED or not may_connect(request.user):
        raise Http404
    active = request.user.mcp_tokens.filter(revoked_at__isnull=True)
    secret = None
    if request.method == 'POST' and request.POST.get('revoke'):
        updated = active.filter(pk=request.POST['revoke'] if request.POST['revoke'].isdigit() else 0).update(
            revoked_at=timezone.now())
        messages.success(request, 'Ulanish bekor qilindi.' if updated else 'Ulanish topilmadi.')
        return redirect('claude_connect')
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()[:60] or 'Claude'
        if active.count() >= MAX_TOKENS:
            messages.error(request, f'Bir vaqtda {MAX_TOKENS} tadan ko‘p ulanish ochib bo‘lmaydi. Keraksizini bekor qiling.')
            return redirect('claude_connect')
        secret, digest = new_token()
        McpToken.objects.create(user=request.user, name=name, digest=digest, hint=secret[-4:])
    base = request.build_absolute_uri(reverse('mcp'))
    return render(request, 'core/claude_connect.html', {
        'page_title': 'Claude’ni ulash', 'subtitle': 'Claude tizimda sizning nomingizdan ishlaydi',
        'tokens': list(active), 'secret': secret, 'endpoint': base,
        'token_url': request.build_absolute_uri(reverse('mcp_token', args=[secret])) if secret else ''})
