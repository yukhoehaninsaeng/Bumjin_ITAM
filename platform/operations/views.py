from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST
from django.db import connection
from .auth import consume_login_attempt
from .policies import employee_for, is_manager


@require_http_methods(["GET", "POST"])
@csrf_protect
def login_page(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()[:128]
        password = request.POST.get("password", "")
        if not consume_login_attempt(request, username):
            return render(
                request,
                "operations/login.html",
                {"error": "로그인 시도가 많습니다. 5분 후 다시 시도하세요."},
                status=429,
            )
        user = (
            authenticate(request, username=username, password=password)
            if 0 < len(password) <= 256
            else None
        )
        if user and (not hasattr(user, "employee") or user.employee.active):
            login(request, user)
            target = request.POST.get("next", "")
            if not url_has_allowed_host_and_scheme(
                target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                target = ""
            allowed = target.startswith("/portal") or (
                target.startswith("/admin") and is_manager(user)
            )
            return redirect(target if allowed else ("/admin" if is_manager(user) else "/portal"))
        return render(
            request,
            "operations/login.html",
            {"error": "아이디 또는 비밀번호가 올바르지 않습니다."},
            status=400,
        )
    return render(request, "operations/login.html")


@require_POST
@login_required
def logout_page(request):
    logout(request)
    return redirect("/login")


@login_required
def home(request):
    return redirect("/admin" if is_manager(request.user) else "/portal")


@login_required
def workspace(request):
    portal = request.path.startswith("/portal")
    if not portal and not is_manager(request.user):
        return render(request, "operations/forbidden.html", status=403)
    if portal and not employee_for(request.user):
        return render(request, "operations/forbidden.html", status=403)
    roles = set(request.user.role_grants.values_list("role", flat=True))
    return render(
        request,
        "operations/workspace.html",
        {"portal": portal, "roles": roles, "person": employee_for(request.user)},
    )


def live(request):
    return JsonResponse({"status": "ok"})


def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM operations_company LIMIT 1")
    except Exception:
        return JsonResponse({"status": "not_ready"}, status=503)
    return JsonResponse({"status": "ready"})


def public_asset(request, asset_id):
    from django.shortcuts import get_object_or_404
    from .models import Asset

    asset = get_object_or_404(Asset, pk=asset_id, company__active=True)
    # Deliberately no employee, price, serial, internal notes or ticket information.
    return render(request, "operations/public_asset.html", {"asset": asset})


@login_required
def legacy_asset(request, asset_id):
    return redirect(
        f"/admin/assets?asset={asset_id}"
        if is_manager(request.user)
        else f"/portal/assets?asset={asset_id}"
    )
