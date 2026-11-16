from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import UserPreferences


class UserTimezoneMiddleware:
    """Presentation timezone only; stored instants remain UTC."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        zone = "Asia/Shanghai"
        language = "zh"
        if request.user.is_authenticated:
            try:
                zone = request.user.preferences.display_timezone
                language = request.user.preferences.display_language
            except UserPreferences.DoesNotExist:
                pass
        request.display_timezone = zone
        request.display_language = language
        timezone.activate(ZoneInfo(zone))
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
