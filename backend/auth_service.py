"""Opaque, server-side administrator and read-only LDAP sessions for the browser login form."""
import hashlib
import hmac
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
import admin_service

COOKIE='bumjin_session'
LOGIN_COOKIE='bumjin_login'
TTL=8*60*60
LOCK=threading.RLock()
SESSIONS={}
PREAUTH={}
ATTEMPTS={}

def cookie_value(env,name):
    try:
        cookie=SimpleCookie();cookie.load(env.get('HTTP_COOKIE',''))
        return cookie[name].value if name in cookie else ''
    except Exception:return ''

def cookie_header(env,name,value,age):
    c=SimpleCookie();c[name]=value;c[name]['path']='/';c[name]['httponly']=True;c[name]['samesite']='Lax';c[name]['max-age']=age
    if env.get('wsgi.url_scheme')=='https' or os.environ.get('PUBLIC_BASE_URL','').lower().startswith('https://'):c[name]['secure']=True
    return ('Set-Cookie',c[name].OutputString())

def stamp(app):
    cfg=admin_service.read_config(app)
    material=cfg.get('password_hash') or os.environ.get('APP_PASSWORD','')
    return hashlib.sha256(str(material).encode()).hexdigest()

def prune(table,limit):
    now=time.monotonic()
    for key in list(table):
        if table[key]['expires']<=now:table.pop(key,None)
    while len(table)>=limit:table.pop(next(iter(table)))

def issue_session(app, username='admin', role='admin'):
    if role not in ('admin','viewer'):raise ValueError('Invalid role')
    token=secrets.token_urlsafe(48);record={'csrf':secrets.token_urlsafe(32),'expires':time.monotonic()+TTL,'stamp':stamp(app),'username':username,'role':role}
    with LOCK:
        prune(SESSIONS,512);SESSIONS[hashlib.sha256(token.encode()).hexdigest()]=record
    return token,record

def current(app,env):
    token=cookie_value(env,COOKIE)
    if not token or len(token)>128:return None
    key=hashlib.sha256(token.encode()).hexdigest()
    with LOCK:
        record=SESSIONS.get(key)
        if not record:return None
        if record['expires']<=time.monotonic() or not hmac.compare_digest(record['stamp'],stamp(app)):
            SESSIONS.pop(key,None);return None
        return record

def remove_session(env):
    token=cookie_value(env,COOKIE)
    with LOCK:SESSIONS.pop(hashlib.sha256(token.encode()).hexdigest(),None)

def issue_preauth(env):
    token=secrets.token_urlsafe(32)
    with LOCK:
        prune(PREAUTH,1024);PREAUTH[token]={'csrf':secrets.token_urlsafe(32),'expires':time.monotonic()+600}
        csrf=PREAUTH[token]['csrf']
    return {'csrf':csrf},cookie_header(env,LOGIN_COOKIE,token,600)

def validate_preauth(env):
    token=cookie_value(env,LOGIN_COOKIE)
    with LOCK:
        record=PREAUTH.get(token)
        if not record or record['expires']<=time.monotonic():return False
        return hmac.compare_digest(str(env.get('HTTP_X_CSRF_TOKEN','')),record['csrf'])

def safe_next(target):
    if not isinstance(target,str) or len(target)>2000 or '\\' in target or any(ord(c)<32 for c in target):return '/'
    try:parsed=urlsplit(target)
    except ValueError:return '/'
    if parsed.scheme or parsed.netloc:return '/'
    path=parsed.path
    return path if path in ('/','/admin','/users') or path.startswith(('/a/','/manage/a/')) else '/'

def login(app,env,payload):
    if not validate_preauth(env):return '403 Forbidden',{'error':'로그인 화면의 유효기간이 지났습니다. 새로고침 후 다시 시도하세요.'},[]
    username=payload.get('username','');password=payload.get('password','')
    if not isinstance(username,str) or not isinstance(password,str) or len(username)>128 or not 1<=len(password)<=256:
        return '400 Bad Request',{'error':'아이디와 비밀번호를 확인하세요.'},[]
    address=env.get('REMOTE_ADDR','local')
    with LOCK:
        now=time.monotonic()
        for key in list(ATTEMPTS):
            if ATTEMPTS[key]['expires']<=now:ATTEMPTS.pop(key,None)
        if address in ATTEMPTS and ATTEMPTS[address]['count']>=8:
            return '429 Too Many Requests',{'error':'로그인 시도가 많습니다. 5분 후 다시 시도하세요.'},[('Retry-After','300')]
    role='admin' if username=='admin' else 'viewer'
    valid=admin_service.password_valid(app,password) if role=='admin' else ldap_password_valid(app,username,password)
    if not valid:
        with LOCK:
            if address not in ATTEMPTS:
                prune(ATTEMPTS,4096);ATTEMPTS[address]={'count':0,'expires':time.monotonic()+300}
            ATTEMPTS[address]['count']+=1
        return '401 Unauthorized',{'error':'아이디 또는 비밀번호가 올바르지 않습니다.'},[]
    remove_session(env);token,record=issue_session(app,username,role)
    with LOCK:
        ATTEMPTS.pop(address,None);PREAUTH.pop(cookie_value(env,LOGIN_COOKIE),None)
    target=safe_next(payload.get('next','/'))
    if role=='viewer' and target.startswith('/manage/a/'):target=target.replace('/manage/a/','/a/',1)
    if role=='viewer' and target in ('/admin','/users'):target='/'
    return '200 OK',{'ok':True,'username':username,'role':role,'next':target},[cookie_header(env,COOKIE,token,TTL),cookie_header(env,LOGIN_COOKIE,'',0)]



def ldap_password_valid(app, username, password):
    """Verify the live LDAP password; LDAP accounts never receive administrator rights."""
    import ldap3
    from ldap3.utils.conv import escape_filter_chars
    conn=None;user_conn=None
    try:
        cfg=admin_service.settings(app)['ldap']
        conn=admin_service.connect_directory(app)
        filt='(&'+cfg['user_filter']+'('+cfg['uid_attr']+'='+escape_filter_chars(username)+'))'
        conn.search(cfg['user_base'] or cfg['base_dn'],filt,attributes=[cfg['uid_attr'],cfg['id_attr']])
        entries=[e for e in conn.response if e['type']=='searchResEntry']
        if conn.result.get('result')!=0 or len(entries)!=1:return False
        entry=entries[0]
        if admin_service.directory_text(entry['attributes'],cfg['uid_attr'])!=username:return False
        if not admin_service.directory_text(entry['attributes'],cfg['id_attr']):return False
        user_conn=ldap3.Connection(conn.server,user=entry['dn'],password=password,auto_bind=True,receive_timeout=15,raise_exceptions=True)
        return bool(user_conn.bound)
    except Exception:return False
    finally:
        for connection in (user_conn,conn):
            if connection:
                try:connection.unbind()
                except Exception:pass
