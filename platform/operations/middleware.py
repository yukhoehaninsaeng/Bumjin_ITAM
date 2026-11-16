from django.contrib.auth import logout
from django.db.models import Q
from .models import Employee


class ActiveEmployeeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and Employee.objects.filter(user=request.user)
            .filter(Q(active=False) | Q(company__active=False))
            .exists()
        ):
            logout(request)
        from zoneinfo import ZoneInfo
        from django.utils import timezone
        from .runtime_settings import display_timezone
        with timezone.override(ZoneInfo(display_timezone())):
            response = self.get_response(request)
        if request.path.startswith(("/api/", "/admin", "/portal")):
            response["Cache-Control"] = "private, no-store"
        return response

