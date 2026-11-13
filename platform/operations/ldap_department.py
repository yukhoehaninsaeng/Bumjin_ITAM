"""Explicit department-only LDAP write-back. No password/group/DN changes."""
import os
import re
import ssl
from contextlib import contextmanager
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import Employee, AuditEvent


class DepartmentSyncError(ValueError):
    pass


def config(name, default=''):
    return getattr(settings, 'ITAM_LDAP', {}).get(name, os.environ.get(name, default))


def attribute(name):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*', name):
        raise DepartmentSyncError('LDAP 속성명을 확인하세요.')
    return name


@contextmanager
def connection():
    import ldap3
    conn = None
    try:
        required = ['LDAP_HOST', 'LDAP_BIND_DN', 'LDAP_BIND_PASSWORD', 'LDAP_BASE_DN']
        if any(not config(key) for key in required):
            raise DepartmentSyncError('LDAP 접속 설정이 필요합니다.')
        tls = ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=config('LDAP_CA_FILE') or None)
        server = ldap3.Server(config('LDAP_HOST'), port=int(config('LDAP_PORT', '636')),
                              use_ssl=True, tls=tls, connect_timeout=10)
        conn = ldap3.Connection(server, user=config('LDAP_BIND_DN'), password=config('LDAP_BIND_PASSWORD'),
                                auto_bind=True, raise_exceptions=True, receive_timeout=10)
        yield conn
    except (ldap3.core.exceptions.LDAPException, OSError) as error:
        raise DepartmentSyncError('LDAP 접속·쓰기 권한·인증서 또는 속성 제약을 확인하세요.') from error
    finally:
        if conn:
            conn.unbind()


def lookup(conn, uid, attributes):
    from ldap3.utils.conv import escape_filter_chars
    query = '(&(objectClass=posixAccount)(uid=' + escape_filter_chars(uid) + '))'
    conn.search(config('LDAP_BASE_DN'), query, attributes=attributes, size_limit=2)
    rows = [row for row in conn.response if row.get('type') == 'searchResEntry']
    if conn.result.get('result') != 0 or len(rows) != 1:
        raise DepartmentSyncError('LDAP 계정이 없거나 중복입니다. 계정 연결을 확인하세요.')
    return rows[0]


def values(row, key):
    value = next((v for k, v in row['attributes'].items() if k.lower() == key.lower()), [])
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)] if value is not None else []


def write_department(employee):
    from ldap3 import MODIFY_REPLACE
    if str(config('LDAP_DEPARTMENT_WRITE_ENABLED', '0')) != '1':
        raise DepartmentSyncError('LDAP 부서 쓰기가 비활성 상태입니다. 설정 확인 후 다시 반영하세요.')
    attr = attribute(config('LDAP_DEPARTMENT_ATTRIBUTE'))
    # Only department-like attributes can be configured; never repurpose account/security fields.
    if attr.lower() not in {'departmentnumber', 'department', 'ou', 'synodepartment'}:
        raise DepartmentSyncError('확인된 부서 속성이 지원 목록에 없습니다. 자동 쓰기를 중단했습니다.')
    identity_attr = attribute(config('LDAP_ID_ATTRIBUTE', 'entryUUID'))
    if not employee.active or not employee.directory_id or employee.directory_source != config('LDAP_DIRECTORY_SOURCE', 'synology'):
        raise DepartmentSyncError('재직 중인 Synology LDAP 연결 계정만 반영할 수 있습니다.')
    with connection() as conn:
        row = lookup(conn, employee.directory_uid, ['uid', identity_attr, attr])
        if values(row, identity_attr) != [employee.directory_id] or values(row, 'uid') != [employee.directory_uid]:
            raise DepartmentSyncError('LDAP 계정 고유 ID가 변경되었습니다. 다시 연결해야 합니다.')
        desired = [employee.organization.name]
        if values(row, attr) != desired:
            if not conn.modify(row['dn'], {attr: [(MODIFY_REPLACE, desired)]}):
                raise DepartmentSyncError('LDAP 부서 저장 실패: 쓰기 권한과 속성을 확인하세요.')
        verified = lookup(conn, employee.directory_uid, [identity_attr, attr])
        if values(verified, identity_attr) != [employee.directory_id] or values(verified, attr) != desired:
            raise DepartmentSyncError('LDAP 저장 결과를 확인하지 못했습니다. 다시 반영하세요.')
    return attr


def sync_state(employee):
    state = dict((employee.groupware_data or {}).get('ldap_department', {}))
    if state.get('status') == 'synced' and state.get('value') != employee.organization.name:
        state['status'] = 'pending'
    return state


def save_department(employee, org, actor, expected_version):
    """Caller locks/authorizes both source employee and destination organization."""
    if employee.version != expected_version:
        raise DepartmentSyncError('다른 변경이 있습니다. 새로고침 후 다시 시도하세요.')
    if not employee.active:
        raise DepartmentSyncError('퇴사자는 부서를 변경할 수 없습니다.')
    before = str(employee.organization_id)
    employee.organization = org
    employee.company = org.company
    data = dict(employee.groupware_data or {})
    data['manual_department'] = {'organization_id': str(org.id), 'actor_id': actor.pk}
    data['company_unconfirmed'] = False
    data['ldap_department'] = {'status': 'pending', 'value': org.name}
    employee.groupware_data = data
    employee.version += 1
    employee.save()
    AuditEvent.objects.create(actor=actor, company=org.company, action='employee.department_changed',
        data={'employee_id': str(employee.id), 'before': before, 'after': str(org.id)})


@transaction.atomic
def sync_employee(employee_id, actor):
    # Serialize retries/edits. Intent is committed by the caller before attempting remote I/O.
    from . import policies
    employee = policies.employees_visible(actor).select_for_update(of=('self',)).get(pk=employee_id)
    state = {'value': employee.organization.name, 'attempted_at': timezone.now().isoformat()}
    try:
        state.update(attribute=write_department(employee), status='synced')
    except (DepartmentSyncError, ValueError) as error:
        state.update(status='failed', message=str(error))
    employee.groupware_data = {**(employee.groupware_data or {}), 'ldap_department': state}
    employee.version += 1
    employee.save(update_fields=['groupware_data', 'version'])
    AuditEvent.objects.create(actor=actor, company=employee.company, action='employee.ldap_department_sync',
        data={'employee_id': str(employee.id), 'status': state['status'], 'value': state['value']})
    return employee, state
