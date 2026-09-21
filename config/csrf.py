from django.conf import settings
from django.contrib.auth import logout
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import redirect, render
from django.urls import reverse


class CsrfMiddleware(CsrfViewMiddleware):
    """CSRF_TRUSTED_ORIGINS=* skips Origin/Referer host matching; the CSRF token is still required."""

    def _origin_verified(self, request):
        return settings.CSRF_TRUST_ALL_ORIGINS or super()._origin_verified(request)

    def _check_referer(self, request):
        if not settings.CSRF_TRUST_ALL_ORIGINS:
            super()._check_referer(request)


def failure(request, reason=''):
    """A stale token means an old page, not an attack: explain it in the app's own words.

    Signing out is the one action worth completing anyway — a forced sign-out
    costs the user nothing, while a refused one leaves them stuck on this page.
    """
    if request.path == reverse('logout'):
        logout(request)
        return redirect('login')
    return render(request, '403_csrf.html', status=403)
