"""Local administrator settings and read-only LDAP import."""
import hashlib, hmac, json, os, secrets, ssl
from pathlib import Path
LDAP_PASSWORD = ''
KEYS = ['host','port','base_dn','user_base','bind_dn','user_filter','uid_attr','name_attr','id_attr','department_attr','ca_file']

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
    return bool(original) and hmac.compare_digest(password,original)
def settings(app):
    cfg=read_config(app)
    defaults={'host':'','port':636,'base_dn':'','user_base':'','bind_dn':'','user_filter':'(objectClass=posixAccount)','uid_attr':'uid','name_attr':'','id_attr':'entryUUID','department_attr':'','ca_file':''}
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
            incoming=payload.get('ldap',{});ldap={k:incoming.get(k,'') for k in KEYS}
            ldap['port']=int(ldap['port'] or 636)
            if not 1<=ldap['port']<=65535:raise ValueError('LDAP 포트가 올바르지 않습니다.')
            if payload.get('bind_password'):LDAP_PASSWORD=str(payload['bind_password'])
            cfg['ldap']=ldap
        else:raise ValueError('설정 작업을 선택하세요.')
        write_config(app,cfg)
        return {'ok':True}
def fetch_directory(app):
    import ldap3
    cfg=settings(app)['ldap'];password=LDAP_PASSWORD or os.environ.get('LDAP_BIND_PASSWORD','')
    if any(not cfg[k] for k in ['host','base_dn','bind_dn','user_filter','uid_attr','name_attr','id_attr']) or not password:
        raise ValueError('LDAP 호스트, Base DN, Bind DN, 검색 필터, 이름·ID 속성과 Bind 비밀번호를 입력하세요.')
    tls=ldap3.Tls(validate=ssl.CERT_REQUIRED,ca_certs_file=cfg['ca_file'] or None)
    server=ldap3.Server(cfg['host'],port=int(cfg['port']),use_ssl=True,tls=tls,connect_timeout=8)
    conn=ldap3.Connection(server,user=cfg['bind_dn'],password=password,auto_bind=True,receive_timeout=15,raise_exceptions=True)
    def first(v):return str(v[0] if isinstance(v,list) and v else v or '')
    try:
        attrs=list({cfg['uid_attr'],cfg['name_attr'],cfg['id_attr'],'mail'}|({cfg['department_attr']} if cfg['department_attr'] else set()))
        entries=list(conn.extend.standard.paged_search(cfg['user_base'] or cfg['base_dn'],cfg['user_filter'],attributes=attrs,paged_size=100,generator=True))
        if conn.result.get('result')!=0:raise ValueError('LDAP 사용자 검색 실패. 기존 목록을 유지합니다.')
        users=[];seen=set();ids=set()
        for e in entries:
            if e['type']!='searchResEntry':continue
            a=e['attributes'];uid=first(a.get(cfg['uid_attr']));stable=first(a.get(cfg['id_attr']))
            if not uid or not stable or uid in seen or stable in ids:raise ValueError('LDAP UID 또는 고유 ID가 없거나 중복됩니다.')
            seen.add(uid);ids.add(stable)
            users.append([uid,first(a.get(cfg['name_attr'])) or uid,first(a.get('mail')),stable,first(a.get(cfg['department_attr'])) if cfg['department_attr'] else '',e['dn']])
        if not users:raise ValueError('조회된 사용자가 없습니다. 기존 목록 유지.')
        orgs=list(conn.extend.standard.paged_search(cfg['base_dn'],'(objectClass=organizationalUnit)',attributes=['ou'],paged_size=100,generator=True))
        if conn.result.get('result')!=0:raise ValueError('LDAP 조직 검색 실패. Base DN과 조회 권한을 확인하세요.')
        organizations=[{'name':first(e['attributes'].get('ou')),'dn':e['dn']} for e in orgs if e['type']=='searchResEntry']
        return users,organizations
    finally:conn.unbind()
def synchronize(app,payload):
    users,orgs=fetch_directory(app)
    with app.LOCK:
        if payload.get('revision')!=app.digest():raise ValueError('Excel이 변경됐습니다. 새로고침 후 동기화하세요.')
        import openpyxl
        old=app.digest();w=openpyxl.load_workbook(app.FILE);s=w['앱_LDAP사용자']
        previous={str(r[0]):str(r[3]) for r in s.iter_rows(min_row=2,max_col=4,values_only=True) if r[0]}
        if any(uid in previous and previous[uid]!=stable for uid,_,_,stable,_,_ in users):raise ValueError('동일 UID의 고유 ID가 달라졌습니다. 계정 재사용 여부를 확인하세요.')
        s.delete_rows(1,s.max_row);s.append(['uid','실명','이메일','디렉터리ID','부서','DN'])
        for row in users:
            r=s.max_row+1
            for c,v in enumerate(row,1):app.literal(s.cell(r,c),v)
        if '앱_LDAP조직' not in w:w.create_sheet('앱_LDAP조직')
        o=w['앱_LDAP조직'];o.delete_rows(1,o.max_row);o.append(['조직명','DN'])
        for entry in orgs:
            r=o.max_row+1;app.literal(o.cell(r,1),entry['name']);app.literal(o.cell(r,2),entry['dn'])
        app.save(w,old)
    return {'users':len(users),'organizations':len(orgs)}
