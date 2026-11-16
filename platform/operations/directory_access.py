"""VPN group leases and NAS passwords share verified LDAP identities and audit events."""
import os
import secrets
import string
from datetime import date,timedelta
from zoneinfo import ZoneInfo
from django.conf import settings
from django.db import transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from .models import Employee,DirectoryLease,AuditEvent,AccessNotice
from .runtime_settings import override,display_timezone
from .ldap_department import connection,lookup,values,config as ldap_config,DepartmentSyncError


def config(key,default=''):
    legacy=getattr(settings,'ITAM_ACCESS',{}).get(key,os.environ.get('ITAM_ACCESS_'+key,default))
    return override('access',key,legacy)


def enabled(key):return str(config(key,'0')).lower() in ('1','true')


def groups():
    return [g.strip() for g in str(config('VPN_GROUPS','00. VPN_User_Always|01. VPN_User_Period')).split('|') if g.strip()]


def local_today():
    return timezone.now().astimezone(ZoneInfo(display_timezone())).date()


def verified(conn,employee,identity=None,allow_inactive=False):
    if ((not allow_inactive and (not employee.active or not employee.company.active or (employee.user_id and not employee.user.is_active))) or not employee.directory_id or not employee.directory_uid
            or employee.directory_source!=ldap_config('LDAP_DIRECTORY_SOURCE','synology')):
        raise ValueError('active_linked_ldap_user_required')
    if employee.directory_uid.casefold() in ('root','admin','administrator') or (employee.user_id and employee.user.is_superuser):
        raise ValueError('protected_directory_account')
    attr=ldap_config('LDAP_ID_ATTRIBUTE','entryUUID')
    row=lookup(conn,employee.directory_uid,['uid',attr])
    if values(row,attr)!=[employee.directory_id] or values(row,'uid')!=[employee.directory_uid] or (identity and identity!=employee.directory_id):
        raise ValueError('directory_identity_changed')
    if row['dn'].casefold()==str(ldap_config('LDAP_BIND_DN')).casefold():raise ValueError('protected_bind_account')
    return row


def group_entry(conn,group):
    from ldap3.utils.conv import escape_filter_chars
    if group not in groups():raise ValueError('group_not_allowed')
    base=config('LDAP_GROUPS_DN') or 'cn=groups,'+ldap_config('LDAP_BASE_DN')
    attr=config('GROUP_MEMBER_ATTRIBUTE','member')
    if attr not in ('member','memberUid'):raise ValueError('unsupported_member_attribute')
    conn.search(base,'(cn='+escape_filter_chars(group)+')',attributes=['cn',attr],size_limit=2)
    rows=[r for r in conn.response if r.get('type')=='searchResEntry']
    if conn.result.get('result')!=0 or len(rows)!=1:raise ValueError('group_not_unique_or_missing')
    return rows[0],attr


def membership(conn,employee,group,identity=None,desired=None):
    from ldap3 import MODIFY_ADD,MODIFY_DELETE
    user=verified(conn,employee,identity,allow_inactive=desired is False);entry,attr=group_entry(conn,group)
    value=user['dn'] if attr=='member' else employee.directory_uid
    present=value.casefold() in [v.casefold() for v in values(entry,attr)]
    if desired is not None and present!=desired:
        if not conn.modify(entry['dn'],{attr:[(MODIFY_ADD if desired else MODIFY_DELETE,[value])]}):
            raise ValueError('ldap_group_write_failed')
        entry,_=group_entry(conn,group)
        present=value.casefold() in [v.casefold() for v in values(entry,attr)]
        if present!=desired:raise ValueError('ldap_group_verification_failed')
    return present


def desired_membership(lease,today=None):
    today=today or local_today()
    return bool(lease.enabled and lease.employee_id and lease.employee.active and lease.employee.company.active
        and (not lease.employee.user_id or lease.employee.user.is_active)
        and lease.starts_on<=today and (lease.permanent or lease.expires_on and today<=lease.expires_on))


def state(lease):
    today=local_today()
    if not lease.enabled:return '해제'
    if not lease.employee_id:return '계정 매핑 필요'
    if not lease.employee.active:return '퇴사/비활성'
    if today<lease.starts_on:return '시작 대기'
    if not lease.permanent and lease.expires_on and today>lease.expires_on:return '만료'
    return '상시' if lease.permanent else '사용 중'


@transaction.atomic
def apply_lease(pk,actor=None):
    lease=DirectoryLease.objects.select_for_update().select_related('employee__company','employee__user').get(pk=pk)
    desired=desired_membership(lease)
    # Retirement must still allow removal of previously linked VPN access. Never grant an inactive identity.
    try:
        if not enabled('VPN_WRITE_ENABLED'):raise ValueError('vpn_write_disabled')
        if not lease.employee_id:raise ValueError('employee_mapping_required')
        employee=lease.employee
        if lease.uid!=employee.directory_uid:raise ValueError('directory_uid_changed')
        with connection() as conn:
            observed=membership(conn,employee,lease.group,lease.directory_id,desired=desired)
        lease.observed_member=observed;lease.sync_state='synced';lease.error_code=''
    except (ValueError,DepartmentSyncError,OSError) as error:
        lease.sync_state='failed';lease.error_code=str(error) if isinstance(error,ValueError) and str(error).replace('_','').isalnum() else type(error).__name__
    except Exception as error:
        # LDAP server diagnostics may contain private attributes. Retain type only.
        lease.sync_state='failed';lease.error_code=type(error).__name__
    lease.last_checked=timezone.now();lease.version+=1;lease.save()
    AuditEvent.objects.create(actor=actor,company=lease.employee.company if lease.employee_id else None,action='directory.vpn_sync',
        data={'lease_id':str(lease.pk),'uid':lease.uid,'group':lease.group,'desired':desired,'status':lease.sync_state,'code':lease.error_code})
    if lease.sync_state=='synced' and desired:
        queue_notice(lease.employee,'vpn_updated',str(lease.pk)+':'+str(lease.version),lease,
            {'group':lease.group,'starts_on':str(lease.starts_on),'expires_on':str(lease.expires_on) if lease.expires_on else '상시'})
    return lease


def queue_notice(employee,kind,event,lease=None,payload=None):
    # Never persist a password or a password-reset capability in mail payloads.
    import hashlib
    AccessNotice.objects.get_or_create(event_key=hashlib.sha256((kind+':'+event).encode()).hexdigest(),
        defaults={'employee':employee,'kind':kind,'lease':lease,'payload':payload or {}})


def process_leases(apply=False):
    report={'checked':0,'changes_needed':0,'failed':0,'unmapped':0}
    for lease in DirectoryLease.objects.select_related('employee__company','employee__user').all():
        report['checked']+=1
        if not lease.employee_id:report['unmapped']+=1;continue
        desired=desired_membership(lease)
        if lease.sync_state!='synced' or lease.observed_member!=desired:
            report['changes_needed']+=1
            if apply:
                lease=apply_lease(lease.pk)
                if lease.sync_state=='failed':report['failed']+=1
        if apply and lease.sync_state=='synced' and lease.observed_member and lease.enabled and desired and not lease.permanent and lease.expires_on:
            days=(lease.expires_on-local_today()).days
            if days in (5,1):queue_notice(lease.employee,'vpn_expiry',f'{lease.pk}:{lease.expires_on}:{days}',lease,
                {'group':lease.group,'expires_on':str(lease.expires_on),'days':days})
    return report


def notify(limit=50):
    from .ticket_mail import smtp,enabled as mail_enabled,address
    from django.core.mail import EmailMessage
    if not mail_enabled():return {'disabled':True}
    report={'sent':0,'failed':0,'skipped':0}
    ids=list(AccessNotice.objects.filter(state='pending',available_at__lte=timezone.now()).values_list('pk',flat=True)[:limit])
    for pk in ids:
        with transaction.atomic():
            item=AccessNotice.objects.select_for_update().select_related('employee__user','employee__company','lease').get(pk=pk)
            if item.state!='pending' or item.available_at>timezone.now():continue
            target=address(item.employee.user) if item.employee.user_id else item.employee.email
            if not target or not item.employee.active or not item.employee.company.active:
                item.state='skipped';report['skipped']+=1
            elif item.kind=='vpn_expiry' and (not item.lease.enabled or str(item.lease.expires_on)!=item.payload['expires_on'] or not desired_membership(item.lease)):
                item.state='skipped';report['skipped']+=1
            else:
                from .ticket_mail import config as mail_config
                subject={'vpn_updated':'VPN 사용 권한 안내','vpn_expiry':'VPN 사용 기간 만료 예정','password_reset':'NAS 계정 비밀번호 초기화 알림','password_changed':'NAS 계정 비밀번호 변경 알림'}[item.kind]
                text=f'{item.employee.name}님의 {subject}입니다.\n계정: {item.employee.directory_uid}\n'
                if item.kind.startswith('vpn_'):text+='\n'.join(k+': '+str(v) for k,v in item.payload.items())
                else:text+='비밀번호 변경이 완료되었습니다. 본인이 요청하지 않았다면 IT 관리자에게 문의하세요. 비밀번호는 메일로 전송하지 않습니다.'
                item.attempts+=1
                try:
                    with smtp() as conn:
                        if EmailMessage(subject,text,mail_config('FROM_EMAIL'),[target],connection=conn,headers={'Auto-Submitted':'auto-generated'}).send()!=1:raise OSError()
                    item.state='sent';item.error_code='';report['sent']+=1
                except Exception as error:
                    item.error_code=type(error).__name__;item.state='failed' if item.attempts>=5 else 'pending'
                    item.available_at=timezone.now()+timedelta(minutes=2**item.attempts);report['failed']+=1
            item.save()
    return report


def generated_password():
    alphabet=string.ascii_letters+string.digits+'!@#%+-_'
    while True:
        password=''.join(secrets.choice(alphabet) for _ in range(20))
        if all(any(c in chars for c in password) for chars in (string.ascii_lowercase,string.ascii_uppercase,string.digits,'!@#%+-_')):return password


def password_operation(employee,actor,password,current=None):
    if not enabled('PASSWORD_WRITE_ENABLED'):raise ValueError('password_write_disabled')
    if not 12<=len(password)<=128:raise ValueError('password_length_12_to_128_required')
    try:validate_password(password,employee.user if employee.user_id else None)
    except DjangoValidationError:raise ValueError('password_policy_rejected')
    with connection() as conn:
        entry=verified(conn,employee)
        if current is not None:
            import ldap3
            check=None
            try:
                check=ldap3.Connection(conn.server,user=entry['dn'],password=current,auto_bind=True,raise_exceptions=True,receive_timeout=10)
                if not check.bound:raise ValueError('current_password_invalid')
            finally:
                if check:check.unbind()
        from ldap3 import MODIFY_REPLACE
        if not conn.modify(entry['dn'],{'userPassword':[(MODIFY_REPLACE,[password])]}):raise ValueError('password_modify_failed')
    # Change Django's session auth hash: other web sessions are invalidated on their next request.
    if employee.user_id:
        user=get_user_model().objects.get(pk=employee.user_id);user.set_unusable_password();user.save(update_fields=['password'])
    AuditEvent.objects.create(actor=actor,company=employee.company,action='directory.password_changed' if current is not None else 'directory.password_reset',
        data={'employee_id':str(employee.pk),'uid':employee.directory_uid})
    queue_notice(employee,'password_changed' if current is not None else 'password_reset',secrets.token_hex(16))
