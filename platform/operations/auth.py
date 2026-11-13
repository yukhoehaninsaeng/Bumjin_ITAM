import hashlib
import os
import ssl
from datetime import timedelta
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.authentication import SessionAuthentication
from .models import Employee, LoginAttempt
from .ldap_department import config


class ApiSessionAuthentication(SessionAuthentication):
    def authenticate_header(self, request):
        return "Session"


class LocalBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        # Directory identities cannot silently fall back to a local password.
        if Employee.objects.filter(user__username=username).exclude(directory_id="").exists():
            return None
        return super().authenticate(request, username=username, password=password, **kwargs)


class DirectoryBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password or len(username) > 128 or len(password) > 256:
            return None
        employee = (
            Employee.objects.filter(
                directory_source=config("LDAP_DIRECTORY_SOURCE", "synology"),
                directory_uid=username,
                active=True,
                company__active=True,
                user__is_active=True,
            )
            .exclude(directory_id="")
            .select_related("user")
            .first()
        )
        if not employee or not config("LDAP_HOST"):
            return None
        import ldap3
        from ldap3.utils.conv import escape_filter_chars

        connections = []
        try:
            tls = ldap3.Tls(
                validate=ssl.CERT_REQUIRED, ca_certs_file=config("LDAP_CA_FILE") or None
            )
            server = ldap3.Server(
                config("LDAP_HOST"),
                port=int(config("LDAP_PORT", "636")),
                use_ssl=True,
                tls=tls,
                connect_timeout=10,
            )
            directory = ldap3.Connection(
                server,
                user=config("LDAP_BIND_DN"),
                password=config("LDAP_BIND_PASSWORD"),
                auto_bind=True,
                receive_timeout=10,
                raise_exceptions=True,
            )
            connections.append(directory)
            attr = config("LDAP_ID_ATTRIBUTE", "entryUUID")
            if not attr.replace("-", "").isalnum():
                return None
            directory.search(
                config("LDAP_BASE_DN"),
                f"({attr}={escape_filter_chars(employee.directory_id)})",
                attributes=["uid", attr],
            )
            rows = [r for r in directory.response if r["type"] == "searchResEntry"]
            if directory.result.get("result") != 0 or len(rows) != 1:
                return None
            row = rows[0]
            uid = row["attributes"].get("uid")
            if isinstance(uid, list):
                uid = uid[0] if len(uid) == 1 else None
            if uid != username:
                return None
            conn = ldap3.Connection(
                server,
                user=row["dn"],
                password=password,
                auto_bind=True,
                receive_timeout=10,
                raise_exceptions=True,
            )
            connections.append(conn)
            return employee.user if conn.bound else None
        except (ldap3.core.exceptions.LDAPException, KeyError, ValueError, OSError):
            return None
        finally:
            for connection in connections:
                connection.unbind()


def consume_login_attempt(request, username):
    """Reserve attempts before authentication, shared across workers; never log credentials."""
    now = timezone.now()
    keys = [
        hashlib.sha256(("ip:" + request.META.get("REMOTE_ADDR", "")).encode()).hexdigest(),
        hashlib.sha256(("user:" + username.casefold()).encode()).hexdigest(),
    ]
    with transaction.atomic():
        for key in sorted(keys):
            row, _ = LoginAttempt.objects.get_or_create(
                key=key, defaults={"expires_at": now + timedelta(minutes=5)}
            )
            row = LoginAttempt.objects.select_for_update().get(pk=row.pk)
            if row.expires_at <= now:
                row.count = 0
                row.expires_at = now + timedelta(minutes=5)
            if row.count >= 8:
                return False
            row.count += 1
            row.save(update_fields=["count", "expires_at"])
    return True

