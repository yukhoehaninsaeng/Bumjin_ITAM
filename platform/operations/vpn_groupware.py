"""Provider adapter for the supplied approval API. Read documents; require explicit review."""
import json
import re
import ssl
from datetime import date
from html import unescape
from urllib.parse import urlsplit,quote
from urllib.request import Request,build_opener,HTTPSHandler,HTTPRedirectHandler,HTTPCookieProcessor
from http.cookiejar import CookieJar
from .directory_access import config
from .models import VpnApproval,AuditEvent


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('groupware_redirect_not_allowed')


def api(path,cookie=None):
    base=str(config('GW_URL')).rstrip('/');url=urlsplit(base)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:raise ValueError('groupware_https_origin_required')
    cookie=cookie or str(config('GW_COOKIE'))
    if not cookie or '\n' in cookie or '\r' in cookie:raise ValueError('groupware_session_required')
    context=ssl.create_default_context(cafile=config('GW_CA_FILE') or None)
    opener=build_opener(NoRedirect(),HTTPSHandler(context=context))
    with opener.open(Request(base+path,headers={'Cookie':cookie,'Accept':'application/json'}),timeout=15) as response:
        raw=response.read(2097153)
        if len(raw)>2097152:raise ValueError('groupware_response_too_large')
        result=json.loads(raw)
    if str(result.get('code'))!='200':raise ValueError('groupware_api_error')
    return result.get('data')


def period(detail):
    text=unescape(re.sub(r'<[^>]*>',' ',str(detail.get('document',{}).get('docBodyContent',''))))
    section=re.search(r'요청\s*기간(.{0,160})',re.sub(r'\s+',' ',text))
    if not section:return None,None,'기간 확인 필요'
    dates=re.findall(r'\d{4}-\d{2}-\d{2}',section.group(1))
    if len(dates)<2:return None,None,'시작일/종료일 확인 필요'
    try:start,end=map(date.fromisoformat,dates[:2])
    except ValueError:return None,None,'날짜 형식 확인 필요'
    if end<start or (end-start).days>90:return None,None,'기간 범위 확인 필요'
    return start,end,''


def fetch_approvals(actor):
    report={'fetched':0,'new':0,'review':0}
    cookie=str(config('GW_COOKIE')) or login()
    # Provider uses zero-based page and offset=page size. Bound the sweep; the next fetch can resume review.
    for page in range(10):
        documents=api(f'/api/approval/todoviewer/all?page={page}&offset=100&property=document.draftedAt&direction=desc&searchtype=formName&keyword=VPN',cookie)
        if not isinstance(documents,list):raise ValueError('unexpected_groupware_list')
        for document in documents:
            if 'VPN' not in document.get('formName','') or document.get('apprStatus')!='APPROVAL' or document.get('docStatus')!='COMPLETE':continue
            key=str(document.get('id',''))
            if not key or len(key)>120:continue
            report['fetched']+=1
            if VpnApproval.objects.filter(pk=key).exists():continue
            detail=api('/api/approval/document/'+quote(key,safe=''),cookie)
            starts,expires,reason=period(detail if isinstance(detail,dict) else {})
            # Account hints are informational; never match or grant by a person's display name.
            VpnApproval.objects.get_or_create(pk=key,defaults={'document_number':str(document.get('docNum',''))[:120],
                'drafter':str(document.get('drafterName',''))[:128],'account_hint':str(document.get('drafterId',''))[:128],
                'starts_on':starts,'expires_on':expires,'reason':reason,'metadata':{'completed_at':str(document.get('completedAt',''))[:40]}})
            report['new']+=1;report['review']+=1
        if len(documents)<100:break
    AuditEvent.objects.create(actor=actor,action='directory.gw_fetched',data=report)
    return report


def login():
    base=str(config('GW_URL')).rstrip('/')
    url=urlsplit(base)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:raise ValueError('groupware_https_origin_required')
    if not config('GW_USERNAME') or not config('GW_PASSWORD'):raise ValueError('groupware_credentials_required')
    jar=CookieJar();opener=build_opener(NoRedirect(),HTTPSHandler(context=ssl.create_default_context(cafile=config('GW_CA_FILE') or None)),HTTPCookieProcessor(jar))
    body=json.dumps({'username':config('GW_USERNAME'),'password':config('GW_PASSWORD'),'captcha':''}).encode()
    with opener.open(Request(base+'/api/login',data=body,headers={'Content-Type':'application/json','Accept':'application/json'}),timeout=15) as response:
        raw=response.read(1048577)
        if len(raw)>1048576:raise ValueError('login_response_too_large')
        data=json.loads(raw)
    if str(data.get('code'))!='200':raise ValueError('groupware_login_failed')
    cookie='; '.join(c.name+'='+c.value for c in jar)
    if not cookie:raise ValueError('groupware_session_missing')
    return cookie
