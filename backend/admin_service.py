"""Local administrator settings and LDAP import and controlled department updates."""
import hashlib, hmac, json, os, secrets, ssl, re
from pathlib import Path
LDAP_PASSWORD = ''
KEYS = ['host','port','base_dn','user_base','bind_dn','user_filter','uid_attr','name_attr','id_attr','department_attr','ca_file','description_attr','verify_tls','allow_department_write']

def config_path(app): return app.FILE.parent/'admin_settings.json'
def read_config(app):
    p=config_path(app)
    return json.loads(p.read_text('utf-8')) if p.exists() else {}
def write_config(app, config):
    p=config_path(app);temp=p.with_suffix('.pending')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(config,f,ensure_ascii=False,indent=2)
    os.replace(temp,p)
def password_valid(app, password):
    cfg=read_config(app)
    if 'password_hash' in cfg:
        digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(cfg['salt']),600000).hex()
        return hmac.compare_digest(digest,cfg['password_hash'])
    original=os.environ.get('APP_PASSWORD','')
    return bool(original) and hmac.compare_digest(password.encode(),original.encode())
def settings(app):
    cfg=read_config(app)
    defaults={'host':'','port':636,'base_dn':'','user_base':'','bind_dn':'','user_filter':'(objectClass=posixAccount)','uid_attr':'uid','name_attr':'','id_attr':'entryUUID','department_attr':'','ca_file':'','description_attr':'description','verify_tls':True,'allow_department_write':False}
    return {'ldap':{**defaults,**cfg.get('ldap',{})},'password_configured':'password_hash' in cfg,'bind_password_available':bool(LDAP_PASSWORD or os.environ.get('LDAP_BIND_PASSWORD'))}
def update_settings(app,payload):
    global LDAP_PASSWORD
    with app.LOCK:
        cfg=read_config(app)
        if payload.get('operation')=='password':
            if not password_valid(app,str(payload.get('current_password',''))):raise ValueError('현재 비밀번호가 올바르지 않습니다.')
            password=str(payload.get('new_password',''))
            if len(password)<12 or len(password)>256:raise ValueError('비밀번호는 12~256자여야 합니다.')
            salt=secrets.token_bytes(16);cfg['salt']=salt.hex()
            cfg['password_hash']=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000).hex()
        elif payload.get('operation')=='ldap':
            incoming=payload.get('ldap',{});ldap={k:incoming.get(k,settings(app)['ldap'].get(k,'')) for k in KEYS}
            for key in ('verify_tls','allow_department_write'):
                ldap[key]=ldap[key] is True or str(ldap[key]).lower()=='true'
            if ldap['allow_department_write'] and ldap['department_attr'].lower() not in ('departmentnumber','department','ou'):
                raise ValueError('LDAP 쓰기는 확인된 부서 속성 departmentNumber, department, ou만 허용합니다.')
            ldap['port']=int(ldap['port'] or 636)
            if not 1<=ldap['port']<=65535:raise ValueError('LDAP 포트가 올바르지 않습니다.')
            if payload.get('bind_password'):LDAP_PASSWORD=str(payload['bind_password'])
            for key in ('uid_attr','name_attr','description_attr','id_attr','department_attr'):
                if ldap[key] and not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]*',str(ldap[key])):raise ValueError('LDAP 속성명 형식이 올바르지 않습니다: '+key)
            cfg['ldap']=ldap
        else:raise ValueError('설정 작업을 선택하세요.')
        write_config(app,cfg)
        return {'ok':True}
def ldap_error(error):
    from ldap3.core.exceptions import LDAPInvalidCredentialsResult, LDAPInsufficientAccessRightsResult
    if isinstance(error, LDAPInvalidCredentialsResult):return 'LDAP 계정 또는 Bind 비밀번호가 올바르지 않습니다.'
    if isinstance(error, LDAPInsufficientAccessRightsResult):return 'LDAP 조회/부서 수정 권한이 없습니다.'
    text=str(error).lower()
    if 'certificate' in text or 'ssl' in text or 'tls' in text:return 'LDAPS 인증서 검증 실패. CA 경로와 호스트 이름 또는 인증서 검증 옵션을 확인하세요.'
    if 'invalid attribute' in text or 'undefinedattribute' in text:return 'LDAP 속성명이 서버 스키마와 맞지 않습니다. 설명·부서 속성을 확인하세요.'
    if 'socket' in text or 'timed out' in text:return 'LDAP 서버 연결 실패. DNS·주소·포트를 확인하세요.'
    return 'LDAP 처리 실패 ('+type(error).__name__+'). 서버 권한과 속성 설정을 확인하세요.'

def connect_directory(app):
    import ldap3
    cfg=settings(app)['ldap'];password=LDAP_PASSWORD or os.environ.get('LDAP_BIND_PASSWORD','')
    missing=[k for k in ['host','base_dn','bind_dn','user_filter','uid_attr','id_attr'] if not cfg[k]]
    if missing:raise ValueError('LDAP 필수 설정 누락: '+', '.join(missing))
    if not password:raise ValueError('Bind 비밀번호가 없습니다. 관리자 설정에서 입력 후 저장하세요. 재시작 시 다시 입력해야 합니다.')
    validate=ssl.CERT_REQUIRED if cfg['verify_tls'] else ssl.CERT_NONE
    try:
        tls=ldap3.Tls(validate=validate,ca_certs_file=(cfg['ca_file'] or None) if cfg['verify_tls'] else None)
        server=ldap3.Server(cfg['host'],port=int(cfg['port']),use_ssl=True,tls=tls,connect_timeout=8)
        return ldap3.Connection(server,user=cfg['bind_dn'],password=password,auto_bind=True,receive_timeout=15,raise_exceptions=True)
    except Exception as e:raise ValueError(ldap_error(e)) from None

def fetch_directory(app):
    cfg=settings(app)['ldap'];conn=connect_directory(app)
    def first(v):return str(v[0] if isinstance(v,list) and v else v or '')
    try:
        attrs=list({cfg['uid_attr'],cfg['name_attr'] or cfg['uid_attr'],cfg['id_attr'],'mail',cfg['description_attr'] or 'description'}|({cfg['department_attr']} if cfg['department_attr'] else set()))
        entries=list(conn.extend.standard.paged_search(cfg['user_base'] or cfg['base_dn'],cfg['user_filter'],attributes=attrs,paged_size=100,generator=True))
        if conn.result.get('result')!=0:raise ValueError('LDAP 사용자 검색 실패. 기존 목록을 유지합니다.')
        users=[];seen=set();ids=set()
        for e in entries:
            if e['type']!='searchResEntry':continue
            a=e['attributes'];uid=first(a.get(cfg['uid_attr']));stable=first(a.get(cfg['id_attr']))
            if not uid or not stable or uid in seen or stable in ids:raise ValueError('LDAP UID 또는 고유 ID가 없거나 중복됩니다.')
            seen.add(uid);ids.add(stable)
            users.append([uid,first(a.get(cfg['name_attr'])) or uid,first(a.get('mail')),stable,first(a.get(cfg['department_attr'])) if cfg['department_attr'] else '',e['dn'],first(a.get(cfg['description_attr'] or 'description'))])
        if not users:raise ValueError('조회된 사용자가 없습니다. 기존 목록 유지.')
        orgs=list(conn.extend.standard.paged_search(cfg['base_dn'],'(objectClass=organizationalUnit)',attributes=['ou'],paged_size=100,generator=True))
        if conn.result.get('result')!=0:raise ValueError('LDAP 조직 검색 실패. Base DN과 조회 권한을 확인하세요.')
        organizations=[{'name':first(e['attributes'].get('ou')),'dn':e['dn']} for e in orgs if e['type']=='searchResEntry']
        return users,organizations
    except ValueError:raise
    except Exception as e:raise ValueError(ldap_error(e)) from None
    finally:conn.unbind()
def synchronize(app,payload):
    users,orgs=fetch_directory(app)
    with app.LOCK:
        if payload.get('revision')!=app.digest():raise ValueError('Excel이 변경됐습니다. 새로고침 후 동기화하세요.')
        import openpyxl
        old=app.digest();w=openpyxl.load_workbook(app.FILE);s=w['앱_LDAP사용자']
        previous={str(r[0]):str(r[3]) for r in s.iter_rows(min_row=2,max_col=4,values_only=True) if r[0]}
        if any(row[0] in previous and previous[row[0]]!=row[3] for row in users):raise ValueError('동일 UID의 고유 ID가 달라졌습니다. 계정 재사용 여부를 확인하세요.')
        s.delete_rows(1,s.max_row);s.append(['uid','표시이름','이메일','디렉터리ID','부서','DN','설명'])
        for row in users:
            r=s.max_row+1
            for c,v in enumerate(row,1):app.literal(s.cell(r,c),v)
        if '앱_LDAP조직' not in w:w.create_sheet('앱_LDAP조직')
        o=w['앱_LDAP조직'];o.delete_rows(1,o.max_row);o.append(['조직명','DN'])
        for entry in orgs:
            r=o.max_row+1;app.literal(o.cell(r,1),entry['name']);app.literal(o.cell(r,2),entry['dn'])
        import organization_service
        organization_service.import_paths(app,w,[row[4] for row in users])
        app.save(w,old)
    return {'users':len(users),'organizations':len(orgs)}
