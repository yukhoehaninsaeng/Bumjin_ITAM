from datetime import date,timedelta
from uuid import UUID
from django.conf import settings
from django.contrib.auth import authenticate,update_session_auth_hash
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError,PermissionDenied,NotFound
from rest_framework.response import Response
from . import directory_access as access
from .admin_settings import require_admin,paginate
from .models import Employee,DirectoryLease,AuditEvent,VpnApproval
from .policies import employee_for


def identifier(value):
    try:return UUID(str(value))
    except (ValueError,TypeError):raise ValidationError('ID를 확인하세요.')


def lease_json(item):
    return {'id':str(item.pk),'employee_id':str(item.employee_id) if item.employee_id else None,'uid':item.uid,'name':item.employee.name if item.employee_id else '미연결',
        'group':item.group,'starts_on':str(item.starts_on),'expires_on':str(item.expires_on) if item.expires_on else '',
        'permanent':item.permanent,'enabled':item.enabled,'description':item.description,'status':access.state(item),'sync_state':item.sync_state,
        'error_code':item.error_code,'version':item.version,'observed_member':item.observed_member,'last_checked':item.last_checked.isoformat() if item.last_checked else None}


@api_view(['GET'])
def accounts(request):
    require_admin(request.user)
    q=str(request.query_params.get('q',''))[:100]
    qs=Employee.objects.filter(active=True,company__active=True).exclude(directory_id='').filter(directory_source=access.ldap_config('LDAP_DIRECTORY_SOURCE','synology')).select_related('company')
    if q:qs=qs.filter(Q(name__icontains=q)|Q(directory_uid__icontains=q))
    return paginate(request,qs.order_by('name','pk'),lambda e:{'id':str(e.pk),'uid':e.directory_uid,'name':e.name,'email':e.email,'company':e.company.code})


@api_view(['GET','POST'])
def leases(request):
    require_admin(request.user)
    if request.method=='POST':
        try:
            employee=Employee.objects.select_related('company').get(pk=identifier(request.data.get('employee_id')),active=True,company__active=True)
            starts=date.fromisoformat(request.data.get('starts_on',''));permanent=request.data.get('permanent') is True
            expires=None if permanent else date.fromisoformat(request.data.get('expires_on',''))
        except (Employee.DoesNotExist,ValueError,TypeError):raise ValidationError('연결된 재직 사용자와 날짜를 확인하세요.')
        group=request.data.get('group');description=str(request.data.get('description','')).strip()
        if not employee.directory_id or group not in access.groups() or len(description)>500 or (expires and expires<starts):raise ValidationError('LDAP 계정·허용 그룹·기간을 확인하세요.')
        with transaction.atomic():
            # Serialize upserts per employee; imported uid/group rows retain their stable record.
            Employee.objects.select_for_update().get(pk=employee.pk)
            item=DirectoryLease.objects.select_for_update().filter(uid=employee.directory_uid,group=group).first()
            if item and request.data.get('expected_version')!=item.version:raise ValidationError('동일 사용자/그룹의 기존 권한을 선택해 최신 버전으로 수정하세요.')
            if item is None:item=DirectoryLease(uid=employee.directory_uid,group=group,starts_on=starts)
            elif item.directory_id and item.directory_id!=employee.directory_id:raise ValidationError('기존 LDAP 고유 ID가 변경되었습니다. 수동 확인이 필요합니다.')
            item.employee=employee;item.directory_id=employee.directory_id;item.starts_on=starts;item.expires_on=expires;item.permanent=permanent
            item.enabled=True;item.description=description;item.sync_state='pending';item.version+=1;item.save()
            AuditEvent.objects.create(actor=request.user,company=employee.company,action='directory.vpn_saved',data={'lease_id':str(item.pk),'uid':item.uid,'group':group,'starts_on':str(starts),'expires_on':str(expires),'permanent':permanent})
        return Response({'data':lease_json(item)},status=201)
    q=str(request.query_params.get('q',''))[:100];qs=DirectoryLease.objects.select_related('employee__company')
    if q:qs=qs.filter(Q(uid__icontains=q)|Q(employee__name__icontains=q)|Q(group__icontains=q))
    result=paginate(request,qs.order_by('uid','group'),lease_json)
    result.data['groups']=access.groups();result.data['write_enabled']=access.enabled('VPN_WRITE_ENABLED')
    return result


@api_view(['POST'])
def lease_action(request,lease_id,action):
    require_admin(request.user)
    with transaction.atomic():
        item=DirectoryLease.objects.select_for_update().select_related('employee__company').filter(pk=lease_id).first()
        if not item:raise NotFound()
        if request.data.get('expected_version')!=item.version:raise ValidationError('권한 정보가 변경되었습니다. 새로고침 후 다시 시도하세요.')
        if action=='revoke':
            item.enabled=False;item.sync_state='pending';item.version+=1;item.save()
            AuditEvent.objects.create(actor=request.user,company=item.employee.company if item.employee_id else None,action='directory.vpn_revoked',data={'lease_id':str(item.pk),'uid':item.uid,'group':item.group})
        elif action!='apply':raise ValidationError('작업을 확인하세요.')
    # Intent persists even if NAS is unavailable. Explicit retry/worker reconciles.
    item=access.apply_lease(item.pk,request.user)
    return Response({'data':lease_json(item)})


def confirm_operator(request):
    from .auth import consume_login_attempt
    password=request.data.get('operator_password','')
    if not isinstance(password,str) or not password or len(password)>256 or not consume_login_attempt(request,request.user.username):raise ValidationError('관리자 비밀번호를 확인하세요.')
    if not authenticate(request,username=request.user.username,password=password):raise ValidationError('관리자 비밀번호가 올바르지 않습니다.')


@api_view(['POST'])
def reset_password(request,employee_id):
    require_admin(request.user)
    if not settings.DEBUG and not request.is_secure():raise PermissionDenied('HTTPS 연결이 필요합니다.')
    confirm_operator(request)
    reason=str(request.data.get('reason','')).strip()
    if not reason or len(reason)>500:raise ValidationError('초기화 사유를 500자 이내로 입력하세요.')
    employee=Employee.objects.select_related('company','user').filter(pk=employee_id,active=True).first()
    if not employee:raise NotFound()
    temporary=access.generated_password()
    try:access.password_operation(employee,request.user,temporary)
    except Exception as error:
        AuditEvent.objects.create(actor=request.user,company=employee.company,action='directory.password_failed',data={'employee_id':str(employee.pk),'code':type(error).__name__})
        return Response({'error':{'message':'초기화 실패. LDAP 쓰기 설정·계정 연결·비밀번호 정책을 확인하세요.'}},status=400)
    AuditEvent.objects.create(actor=request.user,company=employee.company,action='directory.password_reason',data={'employee_id':str(employee.pk),'reason':reason})
    response=Response({'data':{'uid':employee.directory_uid,'temporary_password':temporary,'message':'한 번만 표시합니다. 사용자에게 안전하게 전달하고 본인 비밀번호로 변경하도록 안내하세요.'}})
    response['Cache-Control']='no-store';return response


@api_view(['GET','POST'])
def my_account(request):
    employee=employee_for(request.user)
    if not employee:raise PermissionDenied('연결된 재직 사용자 계정이 필요합니다.')
    if request.method=='GET':
        return Response({'data':{'uid':employee.directory_uid,'linked':bool(employee.directory_id),
            'vpn_links':safe_vpn_links(),'password_enabled':access.enabled('PASSWORD_WRITE_ENABLED'),'leases':[lease_json(x) for x in DirectoryLease.objects.filter(employee=employee).select_related('employee__company')]}})
    if not settings.DEBUG and not request.is_secure():raise PermissionDenied('HTTPS 연결이 필요합니다.')
    current=request.data.get('current_password','');password=request.data.get('new_password','')
    if not isinstance(current,str) or not isinstance(password,str) or not current or len(current)>256 or current==password or password!=request.data.get('confirm_password'):raise ValidationError('현재 비밀번호와 서로 일치하는 새 비밀번호를 확인하세요.')
    from .auth import consume_login_attempt
    if not consume_login_attempt(request,request.user.username):raise ValidationError('시도가 많습니다. 잠시 후 다시 시도하세요.')
    try:access.password_operation(employee,request.user,password,current=current)
    except Exception as error:
        AuditEvent.objects.create(actor=request.user,company=employee.company,action='directory.password_failed',data={'employee_id':str(employee.pk),'code':type(error).__name__})
        return Response({'error':{'message':'변경 실패. 현재 비밀번호·새 비밀번호 정책·LDAP 설정을 확인하세요.'}},status=400)
    request.user.refresh_from_db();update_session_auth_hash(request,request.user)
    return Response({'data':{'ok':True,'message':'비밀번호를 변경했습니다. 다른 웹 세션은 다음 접속 시 해제됩니다.'}})


@api_view(['GET'])
def audit(request):
    require_admin(request.user)
    qs=AuditEvent.objects.filter(action__startswith='directory.').order_by('-created_at','-id')
    return paginate(request,qs,lambda e:{'id':e.id,'actor':e.actor_id,'action':e.action,'data':e.data,'created_at':e.created_at.isoformat()})


@api_view(['GET','POST'])
def approvals(request):
    require_admin(request.user)
    if request.method=='POST':
        action=request.data.get('action')
        if action=='fetch':
            from .vpn_groupware import fetch_approvals
            try:report=fetch_approvals(request.user)
            except Exception as error:
                AuditEvent.objects.create(actor=request.user,action='directory.gw_failed',data={'code':type(error).__name__})
                raise ValidationError('그룹웨어 조회 실패. 연결 설정·세션·API 응답을 확인하세요.')
            return Response({'data':report})
        with transaction.atomic():
            item=VpnApproval.objects.select_for_update().filter(pk=str(request.data.get('document_id',''))).first()
            if not item:raise NotFound()
            if item.status in ('applied','legacy_processed'):raise ValidationError('이미 처리된 문서입니다.')
            if request.data.get('expected_version')!=item.version:raise ValidationError('문서 상태가 변경되었습니다.')
            if action=='exclude':item.status='excluded'
            elif action=='approve':
                employee=Employee.objects.select_related('company').filter(pk=identifier(request.data.get('employee_id')),active=True,company__active=True).exclude(directory_id='').first()
                if not employee or not item.starts_on or not item.expires_on or item.expires_on<item.starts_on:raise ValidationError('사용자와 요청 기간을 확인하세요. 기간이 없는 문서는 수동 권한 등록 후 제외 처리하세요.')
                group=access.config('GW_VPN_GROUP','01. VPN_User_Period')
                if group not in access.groups():raise ValidationError('그룹웨어 VPN 대상 그룹이 허용 목록에 없습니다.')
                Employee.objects.select_for_update().get(pk=employee.pk)
                lease=DirectoryLease.objects.select_for_update().filter(uid=employee.directory_uid,group=group).first()
                if lease and lease.directory_id and lease.directory_id!=employee.directory_id:raise ValidationError('LDAP 고유 ID 변경을 확인하세요.')
                if lease and lease.enabled and (lease.permanent or item.starts_on>lease.expires_on+timedelta(days=1) or item.expires_on<lease.starts_on-timedelta(days=1)):
                    raise ValidationError('기존 상시 권한 또는 떨어진 기간을 자동 합치지 않습니다. 수동으로 기간을 검토하세요.')
                if not lease:lease=DirectoryLease(uid=employee.directory_uid,group=group,starts_on=item.starts_on,expires_on=item.expires_on)
                elif lease.enabled:lease.starts_on=min(lease.starts_on,item.starts_on);lease.expires_on=max(lease.expires_on,item.expires_on)
                else:lease.starts_on=item.starts_on;lease.expires_on=item.expires_on
                lease.employee=employee;lease.directory_id=employee.directory_id;lease.enabled=True;lease.source='groupware';lease.sync_state='pending';lease.version+=1;lease.save()
                item.employee=employee;item.status='applied'
            else:raise ValidationError('작업을 확인하세요.')
            item.version+=1;item.save()
            AuditEvent.objects.create(actor=request.user,action='directory.gw_reviewed',data={'document_id':item.pk,'status':item.status,'employee_id':str(item.employee_id) if item.employee_id else None})
        return Response({'data':{'ok':True,'message':'검토 결과를 저장했습니다. VPN 적용은 별도로 실행하세요.'}})
    return paginate(request,VpnApproval.objects.order_by('-imported_at','document_id'),lambda a:{'id':a.pk,'number':a.document_number,'drafter':a.drafter,'account_hint':a.account_hint,'starts_on':str(a.starts_on) if a.starts_on else '',
        'expires_on':str(a.expires_on) if a.expires_on else '', 'status':a.status,'reason':a.reason,'version':a.version})


def safe_vpn_links():
    from urllib.parse import urlsplit
    links=[]
    for key,label in [('VPN_INTERNAL_URL','VPN 내부 안내'),('VPN_EXTERNAL_URL','VPN 외부 안내')]:
        value=str(access.config(key))
        try:url=urlsplit(value)
        except ValueError:continue
        if url.scheme in ('http','https') and url.hostname and not url.username and not url.password:links.append({'label':label,'url':value})
    return links
