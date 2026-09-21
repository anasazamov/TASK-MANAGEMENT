from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware


class CsrfMiddleware(CsrfViewMiddleware):
    """CSRF_TRUSTED_ORIGINS=* skips Origin/Referer host matching; the CSRF token is still required."""

    def _origin_verified(self, request):
        return settings.CSRF_TRUST_ALL_ORIGINS or super()._origin_verified(request)

    def _check_referer(self, request):
        if not settings.CSRF_TRUST_ALL_ORIGINS:
            super()._check_referer(request)
