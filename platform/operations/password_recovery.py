"""Anonymous recovery is enabled only for verified, active directory users with registered mail."""
import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlencode
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import Employee,PasswordRecovery,AccessNotice,AuditEvent
from . import directory_access as access
from .runtime_settings import cipher
from .ticket_mail import address,config as mail_config,enabled as mail_enabled

GENERIC='등록된 회사 계정과 이메일이 일치하면 재설정 안내를 보내드립니다. 메일을 받을 수 없다면 IT 관리자에게 문의하세요.'


def base_url():
    from urllib.parse import urlsplit
    from django.conf import settings
    value=str(mail_config('PUBLIC_BASE_URL')).rstrip('/')
    parts=urlsplit(value)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment or any(c.isspace() for c in value):raise ValueError('PUBLIC_BASE_URL')
    if not settings.DEBUG and parts.scheme!='https':raise ValueError('HTTPS_REQUIRED')
    return value


def eligible(employee):
    return bool(employee and employee.active and employee.company.active and employee.user_id and employee.user.is_active
        and not employee.user.is_superuser and employee.directory_uid.casefold() not in ('root','admin','administrator')
        and employee.directory_id and employee.directory_source==access.ldap_config('LDAP_DIRECTORY_SOURCE','synology'))


def credential(employee):return hashlib.sha256(employee.user.password.encode()).hexdigest()


def available():return access.enabled('PASSWORD_RECOVERY_ENABLED') and access.enabled('PASSWORD_WRITE_ENABLED') and mail_enabled()


def request_recovery(identifier,email):
    if not available():return
    people=list(Employee.objects.filter(Q(directory_uid=identifier)|Q(user__username=identifier)).select_related('company','user')[:2])
    if len(people)!=1:return
    employee=people[0]
    if not eligible(employee) or not address(employee.user) or address(employee.user)!=email.strip().lower():return
    # Validate crypto/link settings before creating a queued recovery.
    encrypt=cipher();base_url()
    with transaction.atomic():
        employee=Employee.objects.select_for_update().select_related('company','user').get(pk=employee.pk)
        if not eligible(employee) or not address(employee.user) or address(employee.user)!=email.strip().lower():return
        if PasswordRecovery.objects.filter(employee=employee,created_at__gte=timezone.now()-timedelta(minutes=30)).count()>=3:return
        PasswordRecovery.objects.filter(employee=employee,used_at__isnull=True).update(used_at=timezone.now())
        token=secrets.token_urlsafe(32)
        item=PasswordRecovery.objects.create(employee=employee,token_hash=hashlib.sha256(token.encode()).hexdigest(),address=address(employee.user),
            directory_id=employee.directory_id,uid=employee.directory_uid,credential_hash=credential(employee),expires_at=timezone.now()+timedelta(minutes=30))
        access.queue_notice(employee,'password_recovery',str(item.pk),payload={'recovery_id':str(item.pk),'encrypted_token':encrypt.encrypt(token.encode()).decode()})
        AuditEvent.objects.create(company=employee.company,action='directory.recovery_requested',data={'employee_id':str(employee.pk)})


def valid(item,employee):
    return (available() and eligible(employee) and not item.used_at and item.expires_at>timezone.now()
        and item.address==address(employee.user) and item.directory_id==employee.directory_id and item.uid==employee.directory_uid
        and hmac.compare_digest(item.credential_hash,credential(employee)))


def delivery(item):
    record=PasswordRecovery.objects.select_related('employee__company','employee__user').filter(pk=item.payload.get('recovery_id')).first()
    if not record or not valid(record,record.employee):return None
    token=cipher().decrypt(item.payload['encrypted_token'].encode()).decode()
    if not hmac.compare_digest(record.token_hash,hashlib.sha256(token.encode()).hexdigest()):return None
    return base_url()+'/password-recovery#'+urlencode({'id':str(record.pk),'token':token})


def finish(record_id,token,password):
    if not isinstance(token,str) or not 30<=len(token)<=128 or not isinstance(password,str) or not 12<=len(password)<=128:raise ValueError('invalid_recovery')
    # Consume before remote I/O: a failed or interrupted NAS change cannot replay the same capability.
    with transaction.atomic():
        record=PasswordRecovery.objects.select_for_update().select_related('employee__company','employee__user').filter(pk=record_id).first()
        if not record or not hmac.compare_digest(record.token_hash,hashlib.sha256(token.encode()).hexdigest()) or not valid(record,record.employee):raise ValueError('invalid_recovery')
        employee=record.employee
        record.used_at=timezone.now();record.save(update_fields=['used_at'])
    try:access.password_operation(employee,employee.user,password)
    except Exception as error:
        AuditEvent.objects.create(company=employee.company,action='directory.recovery_failed',data={'employee_id':str(employee.pk),'code':type(error).__name__})
        raise ValueError('recovery_change_failed') from error
    PasswordRecovery.objects.filter(employee=employee,used_at__isnull=True).update(used_at=timezone.now())
    AuditEvent.objects.create(company=employee.company,action='directory.password_recovered',data={'employee_id':str(employee.pk)})


def page(request):
    from django.shortcuts import render
    response=render(request,'operations/password_recovery.html')
    response['Cache-Control']='no-store';response['Referrer-Policy']='no-referrer'
    return response


def submit(request):
    import json
    from uuid import UUID
    from django.conf import settings
    from django.http import JsonResponse
    from .auth import consume_login_attempt
    if not settings.DEBUG and not request.is_secure():return JsonResponse({'error':'HTTPS 연결이 필요합니다.'},status=403)
    try:
        if len(request.body)>8192:raise ValueError()
        data=json.loads(request.body)
        if not isinstance(data,dict):raise ValueError()
    except (ValueError,UnicodeError):return JsonResponse({'error':'입력 내용을 확인하세요.'},status=400)
    action=data.get('action','request');identity=str(data.get('account',''))[:128]
    if not consume_login_attempt(request,'recovery:'+identity if action=='request' else 'recovery-complete'):
        return JsonResponse({'message':GENERIC}) if action=='request' else JsonResponse({'error':'시도가 많습니다. 잠시 후 다시 요청하세요.'},status=429)
    if action=='request':
        try:request_recovery(identity,str(data.get('email',''))[:254])
        except Exception as error:
            # No account, address, token, request body or mail server detail in logs/responses.
            AuditEvent.objects.create(action='directory.recovery_setup_failed',data={'code':type(error).__name__})
        return JsonResponse({'message':GENERIC})
    if action!='complete' or data.get('password')!=data.get('confirm_password'):return JsonResponse({'error':'새 비밀번호 확인 값을 확인하세요.'},status=400)
    try:finish(UUID(str(data.get('id',''))),data.get('token',''),data.get('password',''))
    except (ValueError,TypeError):return JsonResponse({'error':'링크가 유효하지 않거나 변경에 실패했습니다. 새 링크를 요청하거나 IT 관리자에게 문의하세요.'},status=400)
    return JsonResponse({'message':'NAS 비밀번호를 변경했습니다. 새 비밀번호로 로그인하세요.'})


from django.views.decorators.csrf import csrf_protect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET,require_POST
page=require_GET(page)
submit=never_cache(csrf_protect(require_POST(submit)))
