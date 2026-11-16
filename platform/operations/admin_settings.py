"""System-wide configuration and explicit scoped grants; only global system administrators."""
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from .models import SystemConfiguration, RoleGrant, Employee, Company, SupportTeam, AuditEvent
from .runtime_settings import cipher, decrypt, encryption_key, display_timezone

MAIL_FIELDS={
 'ENABLED':'bool','PUBLIC_BASE_URL':'text','FROM_EMAIL':'email','REPLY_ADDRESS':'email',
 'SMTP_HOST':'host','SMTP_PORT':'port','SMTP_USER':'text','SMTP_PASSWORD':'secret','SMTP_USE_TLS':'bool','SMTP_USE_SSL':'bool',
 'INBOUND_ENABLED':'bool','IMAP_HOST':'host','IMAP_PORT':'port','IMAP_USER':'text','IMAP_PASSWORD':'secret',
 'IMAP_FOLDER':'text','IMAP_CA_FILE':'text','AUTH_SERV_ID':'text'}
LDAP_FIELDS={
 'LDAP_HOST':'host','LDAP_PORT':'port','LDAP_BASE_DN':'text','LDAP_BIND_DN':'text','LDAP_BIND_PASSWORD':'secret',
 'LDAP_CA_FILE':'text','LDAP_DIRECTORY_SOURCE':'text','LDAP_ID_ATTRIBUTE':'attribute','LDAP_USER_FILTER':'text',
 'LDAP_DEPARTMENT_ATTRIBUTE':'attribute','LDAP_DEPARTMENT_WRITE_ENABLED':'bool'}
ACCESS_FIELDS={'VPN_WRITE_ENABLED':'bool','PASSWORD_WRITE_ENABLED':'bool','LDAP_GROUPS_DN':'text','GROUP_MEMBER_ATTRIBUTE':'attribute',
 'VPN_GROUPS':'text','GW_VPN_GROUP':'text','GW_URL':'text','GW_USERNAME':'text','GW_PASSWORD':'secret','GW_COOKIE':'secret','GW_CA_FILE':'text','VPN_INTERNAL_URL':'text','VPN_EXTERNAL_URL':'text'}
SCHEMAS={'mail':MAIL_FIELDS,'ldap':LDAP_FIELDS,'system':{'TIME_ZONE':'timezone'},'access':ACCESS_FIELDS}
DEFAULTS={'mail':{'ENABLED':'0','SMTP_PORT':'587','SMTP_USE_TLS':'1','SMTP_USE_SSL':'0','INBOUND_ENABLED':'0','IMAP_PORT':'993','IMAP_FOLDER':'INBOX'},
 'ldap':{'LDAP_PORT':'636','LDAP_DIRECTORY_SOURCE':'synology','LDAP_ID_ATTRIBUTE':'entryUUID','LDAP_USER_FILTER':'(objectClass=posixAccount)','LDAP_DEPARTMENT_WRITE_ENABLED':'0'},'system':{'TIME_ZONE':'Asia/Seoul'},'access':{'VPN_WRITE_ENABLED':'0','PASSWORD_WRITE_ENABLED':'0','GROUP_MEMBER_ATTRIBUTE':'member','VPN_GROUPS':'00. VPN_User_Always|01. VPN_User_Period','GW_VPN_GROUP':'01. VPN_User_Period'}}


def require_admin(user):
    if not user.is_authenticated or not user.is_active or not RoleGrant.objects.filter(user=user,role='system_admin',scope_type='GLOBAL',company__isnull=True).exists():
        raise PermissionDenied('전체 범위의 시스템 관리자 권한이 필요합니다.')


def public_group(group):
    from .runtime_settings import row
    from .ticket_mail import config as mail_config
    from .ldap_department import config as ldap_config
    from .directory_access import config as access_config
    item=row(group);result={};configured={}
    for key,kind in SCHEMAS[group].items():
        if kind=='secret':
            # Presence only; no decryption and no plaintext in API responses.
            legacy=getattr(settings,{'mail':'ITAM_MAIL','ldap':'ITAM_LDAP','access':'ITAM_ACCESS'}[group],{})
            import os
            configured[key]=bool(item and key in item.values.get('_secret_keys',[])) or bool(legacy.get(key) or os.environ.get({'mail':'ITAM_MAIL_','ldap':'','access':'ITAM_ACCESS_'}[group]+key))
        elif group=='system':result[key]=display_timezone()
        else:result[key]={'mail':mail_config,'ldap':ldap_config,'access':access_config}[group](key,DEFAULTS[group].get(key,''))
    return {'values':result,'secret_configured':configured,'version':item.version if item else 0}


def validate(group,values):
    if not isinstance(values,dict) or set(values)-SCHEMAS[group].keys():raise ValidationError('설정 항목을 확인하세요.')
    cleaned={}
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError as DjangoValidationError
    for key,value in values.items():
        kind=SCHEMAS[group][key]
        if not isinstance(value,(str,int,bool)):raise ValidationError('설정값 형식을 확인하세요.')
        value=str(value).strip() if kind!='secret' else str(value)
        if len(value)>2048 or '\n' in value or '\r' in value:raise ValidationError('설정값 길이/줄바꿈을 확인하세요.')
        if kind=='bool':
            if value.lower() not in ('0','1','true','false'):raise ValidationError('켜짐/꺼짐 값을 확인하세요.')
            value='1' if value.lower() in ('1','true') else '0'
        if kind=='port':
            if not value.isdigit() or not 1<=int(value)<=65535:raise ValidationError('포트는 1~65535입니다.')
        if kind=='host' and value and not re.fullmatch(r'[A-Za-z0-9_.:\-]+',value):raise ValidationError('호스트명 또는 IP만 입력하세요.')
        if kind=='attribute' and value and not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*',value):raise ValidationError('LDAP 속성명을 확인하세요.')
        if kind=='email' and value:
            try:validate_email(value)
            except DjangoValidationError:raise ValidationError('이메일 주소를 확인하세요.')
        if kind=='timezone':
            try:ZoneInfo(value)
            except (ZoneInfoNotFoundError,ValueError):raise ValidationError('IANA 시간대를 선택하세요.')
        if key in ('PUBLIC_BASE_URL','GW_URL','VPN_INTERNAL_URL','VPN_EXTERNAL_URL') and value:
            from urllib.parse import urlsplit
            try:url=urlsplit(value)
            except ValueError:raise ValidationError('프로그램 접속 주소를 확인하세요.')
            if url.scheme not in ('https','http') or not url.hostname or url.username or url.password or (url.path not in ('','/') if key in ('PUBLIC_BASE_URL','GW_URL') else False) or url.query or url.fragment:raise ValidationError('프로그램의 http/https 기본 주소를 입력하세요.')
            value=value.rstrip('/')
        cleaned[key]=value
    return cleaned


@api_view(['GET','PATCH'])
def configuration(request,group):
    require_admin(request.user)
    if group not in SCHEMAS:raise ValidationError('설정 그룹을 확인하세요.')
    if request.method=='PATCH':
        if not isinstance(request.data,dict):raise ValidationError('JSON 객체를 입력하세요.')
        values=validate(group,request.data.get('values',{}))
        if not isinstance(request.data.get('version'),int):raise ValidationError('현재 설정 버전이 필요합니다.')
        with transaction.atomic():
            item,created=SystemConfiguration.objects.get_or_create(pk=group)
            item=SystemConfiguration.objects.select_for_update().get(pk=group)
            expected=0 if created else item.version
            if request.data['version']!=expected:raise ValidationError('설정이 변경되었습니다. 새로고침 후 다시 저장하세요.')
            secrets=None;changed=[]
            for key,value in values.items():
                if SCHEMAS[group][key]=='secret':
                    if not value:continue # Blank preserves saved or deployment password.
                    if secrets is None:
                        try:secrets=decrypt(item.secrets)
                        except ValueError as error:raise ValidationError(str(error))
                    secrets[key]=value;changed.append(key)
                else:item.values[key]=value;changed.append(key)
            if secrets is not None:
                try:item.secrets=cipher().encrypt(json.dumps(secrets).encode()).decode()
                except ValueError as error:raise ValidationError(str(error))
                item.values['_secret_keys']=list(secrets)
            item.version=1 if created else item.version+1;item.save()
            AuditEvent.objects.create(actor=request.user,action='settings.updated',data={'group':group,'fields':changed,'version':item.version})
    result=public_group(group)
    if group=='system':result['timezones']=sorted(available_timezones())
    result['encryption_ready']=bool(encryption_key())
    return Response({'data':result})


@api_view(['POST'])
def connection_test(request,group):
    require_admin(request.user)
    # Test saved settings only; never send a message or modify LDAP.
    ok=False;code='unsupported'
    try:
        if group=='mail':
            from .ticket_mail import smtp
            with smtp() as conn:conn.open()
        elif group=='ldap':
            from .ldap_department import connection,config
            with connection() as conn:
                conn.search(config('LDAP_BASE_DN'),'(objectClass=*)',search_scope='BASE',attributes=['objectClass'],size_limit=1)
                if conn.result.get('result')!=0:raise ValueError('search_failed')
        else:raise ValidationError('SMTP 또는 LDAP 테스트만 지원합니다.')
        ok=True;code='connected'
    except ValidationError:raise
    except Exception as error:code=type(error).__name__
    AuditEvent.objects.create(actor=request.user,action='settings.connection_test',data={'group':group,'ok':ok,'code':code})
    return Response({'data':{'ok':ok,'message':'연결 성공' if ok else '연결 실패: 저장한 설정·인증서·접속 권한을 확인하세요.','code':code}})


@api_view(['GET'])
def directory_accounts(request):
    require_admin(request.user)
    query=str(request.query_params.get('q','')).strip()[:100]
    qs=Employee.objects.filter(active=True,company__active=True,user__is_active=True).exclude(directory_id='').select_related('user','company')
    if query:qs=qs.filter(Q(name__icontains=query)|Q(directory_uid__icontains=query))
    return paginate(request,qs.order_by('name','pk'),lambda e:{'id':str(e.id),'name':e.name,'uid':e.directory_uid,'company':e.company.code,
        'grants':[{'id':g.id,'role':g.role,'scope_type':g.scope_type,'company_id':str(g.company_id) if g.company_id else None} for g in e.user.role_grants.all()]})


@api_view(['GET','POST','DELETE'])
def grants(request):
    require_admin(request.user)
    if request.method=='GET':
        return Response({'data':{'companies':[{'id':str(c.id),'code':c.code} for c in Company.objects.filter(active=True)],
            'teams':[{'id':str(t.id),'name':t.name,'companies':[str(c) for c in t.companies.values_list('id',flat=True)]} for t in SupportTeam.objects.filter(active=True)],
            'roles':[r[0] for r in RoleGrant.ROLES]}})
    if not isinstance(request.data,dict):raise ValidationError('JSON 객체를 입력하세요.')
    from uuid import UUID
    try:
        if request.method=='DELETE':
            if type(request.data.get('grant_id')) is not int:raise ValueError()
        else:
            UUID(str(request.data.get('employee_id','')))
            if request.data.get('company_id'):UUID(str(request.data['company_id']))
            if request.data.get('team_id'):UUID(str(request.data['team_id']))
    except (ValueError,TypeError):raise ValidationError('사용자·권한·법인·지원팀 ID를 확인하세요.')
    with transaction.atomic():
        SystemConfiguration.objects.get_or_create(pk='roles')
        SystemConfiguration.objects.select_for_update().get(pk='roles') # Serialize all privilege writes.
        require_admin(request.user)
        if request.method=='DELETE':
            grant=RoleGrant.objects.filter(pk=request.data.get('grant_id')).select_related('user').first()
            if not grant:raise ValidationError('권한을 찾을 수 없습니다.')
            if grant.role=='system_admin' and grant.scope_type=='GLOBAL':
                if grant.user_id==request.user.id:raise ValidationError('본인의 전체 시스템 관리자 권한은 여기서 해제할 수 없습니다.')
                if RoleGrant.objects.filter(role='system_admin',scope_type='GLOBAL',user__is_active=True).exclude(pk=grant.pk).count()<1:raise ValidationError('마지막 시스템 관리자는 해제할 수 없습니다.')
            data={'user_id':grant.user_id,'role':grant.role,'scope':grant.scope_type,'company_id':str(grant.company_id) if grant.company_id else None}
            grant.delete();action='permissions.revoked'
        else:
            employee=Employee.objects.filter(pk=request.data.get('employee_id'),active=True,company__active=True,user__is_active=True).exclude(directory_id='').select_related('user').first()
            if not employee:raise ValidationError('연결된 재직 LDAP 사용자를 선택하세요.')
            role=request.data.get('role');scope=request.data.get('scope_type')
            if role not in dict(RoleGrant.ROLES) or scope not in ('COMPANY','GLOBAL'):raise ValidationError('역할과 범위를 확인하세요.')
            company=None
            if scope=='COMPANY':
                company=Company.objects.filter(pk=request.data.get('company_id'),active=True).first()
                if not company:raise ValidationError('법인을 선택하세요.')
            if role=='system_admin' and scope!='GLOBAL':raise ValidationError('이 설정 화면의 시스템 관리자는 전체 범위를 사용합니다.')
            team=None
            if role=='support_agent':
                team=SupportTeam.objects.filter(pk=request.data.get('team_id'),active=True).first()
                if not team or (company and not team.companies.filter(pk=company.pk).exists()):raise ValidationError('법인에 맞는 지원팀을 선택하세요.')
            grant,_=RoleGrant.objects.get_or_create(user=employee.user,role=role,scope_type=scope,company=company)
            if team:team.members.add(employee.user)
            data={'user_id':employee.user_id,'role':role,'scope':scope,'company_id':str(company.pk) if company else None,'team_id':str(team.pk) if team else None};action='permissions.granted'
        AuditEvent.objects.create(actor=request.user,action=action,data=data)
    return Response({'data':{'ok':True}})


@api_view(['GET'])
def history(request):
    require_admin(request.user)
    qs=AuditEvent.objects.filter(Q(action__startswith='settings.')|Q(action__startswith='permissions.')).order_by('-created_at','-id')
    return paginate(request,qs,lambda e:{'id':e.id,'actor':e.actor_id,'action':e.action,'data':e.data,'created_at':e.created_at.isoformat()})


def paginate(request,qs,render):
    try:
        page=max(1,int(request.query_params.get('page',1)))
        size=min(100,max(1,int(request.query_params.get('page_size',30))))
    except (ValueError,TypeError):raise ValidationError('페이지 값을 확인하세요.')
    return Response({'data':[render(e) for e in qs[(page-1)*size:page*size]],'meta':{'page':page,'page_size':size,'total':qs.count()}})

