"""Database-backed login throttling without storing clear-text IPs or emails."""
import hashlib
import hmac
import ipaddress
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LoginThrottle


WINDOW = timedelta(minutes=15)
BLOCK = timedelta(minutes=15)
ACCOUNT_MAX_FAILURES = 5
IP_MAX_FAILURES = 20


def _digest(kind, value):
    return hmac.new(settings.SECRET_KEY.encode(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def _keys(request, email):
    # The production proxy overwrites X-Real-IP; never trust X-Forwarded-For.
    ip = request.META.get("REMOTE_ADDR", "unknown")
    if settings.TRUSTED_REVERSE_PROXY:
        proposed = request.META.get("HTTP_X_REAL_IP", "")
        try:
            ip = str(ipaddress.ip_address(proposed))
        except ValueError:
            pass
    return ((_digest("account", email.strip().lower()), ACCOUNT_MAX_FAILURES),
            (_digest("ip", ip), IP_MAX_FAILURES))


def is_blocked(request, email):
    now = timezone.now()
    return LoginThrottle.objects.filter(
        key_digest__in=[key for key, _ in _keys(request, email)],
        blocked_until__gt=now,
    ).exists()


def record_failure(request, email):
    now = timezone.now()
    for key, limit in _keys(request, email):
        with transaction.atomic():
            LoginThrottle.objects.get_or_create(key_digest=key, defaults={"window_start": now})
            row = LoginThrottle.objects.select_for_update().get(key_digest=key)
            if row.window_start <= now - WINDOW:
                row.window_start = now
                row.failed_count = 0
                row.blocked_until = None
            row.failed_count += 1
            if row.failed_count >= limit:
                row.blocked_until = now + BLOCK
            row.save(update_fields=("window_start", "failed_count", "blocked_until"))


def clear_account_failure(email):
    LoginThrottle.objects.filter(key_digest=_digest("account", email.strip().lower())).delete()
