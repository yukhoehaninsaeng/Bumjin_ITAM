"""Bumjin Excel-backed pilot. Single process, no database server."""
import base64, copy, datetime as dt, hashlib, hmac, io, json, os, secrets, shutil, threading, uuid
from pathlib import Path
from urllib.parse import urlsplit
from wsgiref.simple_server import make_server
import openpyxl
from openpyxl.workbook.properties import CalcProperties

ROOT = Path(__file__).resolve().parent
FILE = Path(os.environ.get('ASSET_FILE', str(ROOT/'data/Bumjin_IT자산관리대장.xlsx')))
LOCK = threading.RLock()
CONFIG = {'BJM': ('3.BJM PC 현황','W','X','U','V'), 'BJE': ('4.BJE PC 현황','Y','Z','V','X'), 'BJC': ('5.BJC PC 현황','Y','Z','V','X')}
BASE = {'number':'B','department':'C','user':'D','type':'E','maker':'F','model':'G','serial':'H','cpu':'I','bits':'J','ram':'K','ssd':'L','hdd':'M','gpu':'N','os':'O','edition':'P','office':'Q','hangul':'R'}

def digest(): return hashlib.sha256(FILE.read_bytes()).hexdigest()
def value(v): return v.isoformat()[:10] if isinstance(v,(dt.datetime,dt.date)) else str(v or '')
def literal(cell, v):
    cell.value = str(v or '')[:2000]
    cell.data_type = 's'  # user data cannot become an Excel formula
def save(w, expected):
    if digest()!=expected: raise ValueError('엑셀이 외부에서 변경됐습니다. 새로고침 후 다시 시도하세요.')
    directory=FILE.parent/'backups'; directory.mkdir(exist_ok=True)
    shutil.copy2(FILE,directory/(dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.xlsx'))
    temp=FILE.with_name(FILE.stem+'.pending.xlsx')
    try:
        w.calculation=CalcProperties(fullCalcOnLoad=True)
        w.save(temp)
        check=openpyxl.load_workbook(temp); check.close()
        if digest()!=expected: raise ValueError('저장 중 외부 수정 감지. 앱 쓰기를 중지하고 파일을 확인하세요.')
        os.replace(temp,FILE)
    finally: temp.unlink(missing_ok=True)

def initialize():
    with LOCK:
        old=digest(); w=openpyxl.load_workbook(FILE); changed=False
        for division,(name,*_) in CONFIG.items():
            s=w[name]
            if s['AZ5'].value not in (None,'APP_ASSET_ID'): raise ValueError('AZ열이 이미 사용 중입니다.')
            if s['BA5'].value not in (None,'APP_LDAP_UID'): raise ValueError('BA열이 이미 사용 중입니다.')
            for col,label in [('AZ','APP_ASSET_ID'),('BA','APP_LDAP_UID')]:
                if s[f'{col}5'].value!=label: s[f'{col}5']=label; changed=True
                s.column_dimensions[col].hidden=True
            for r in range(6,s.max_row+1):
                if s[f'B{r}'].value and not s[f'AZ{r}'].value:
                    s[f'AZ{r}']=str(uuid.uuid4()); changed=True
        for name,headers in [('앱_이력',['시각','자산ID','유형','이전','이후']),('앱_LDAP사용자',['uid','실명','이메일','디렉터리ID'])]:
            if name not in w: w.create_sheet(name).append(headers); changed=True
        if changed: save(w,old)

def assets(w):
    out=[]
    for division,(name,date,status,grade,note) in CONFIG.items():
        s=w[name]
        for r in range(6,s.max_row+1):
            if not s[f'B{r}'].value: continue
            a={k:value(s[f'{c}{r}'].value) for k,c in BASE.items()}
            a.update(id=value(s[f'AZ{r}'].value),uid=value(s[f'BA{r}'].value),division=division,row=r,
                     date=value(s[f'{date}{r}'].value),status=value(s[f'{status}{r}'].value),note=value(s[f'{note}{r}'].value),
                     date_label={'BJM':'구매일자','BJE':'구입년월','BJC':'제조일자'}[division])
            a['grade']='미확인'
            try:
                d=dt.date.fromisoformat(a['date']); t=dt.date.today(); years=t.year-d.year-((t.month,t.day)<(d.month,d.day))
                if years>=0:a['grade']='D' if years>=10 else 'C' if years>=5 else 'B' if years>=3 else 'A'
            except ValueError: pass
            out.append(a)
    return out

def snapshot():
    with LOCK:
        rev=digest(); w=openpyxl.load_workbook(FILE)
        users=[dict(zip(['uid','name','email','directory_id'],map(value,r))) for r in w['앱_LDAP사용자'].iter_rows(min_row=2,max_col=4,values_only=True) if r[0]]
        history=[list(map(value,r)) for r in w['앱_이력'].iter_rows(min_row=2,max_col=5,values_only=True)]
        return {'assets':assets(w),'users':users,'revision':rev,'history':history[-300:][::-1],
                'base_url':os.environ.get('PUBLIC_BASE_URL','http://127.0.0.1:8080'), 'mode':'로컬 테스트 엑셀 · Drive 자동 반영 미연결'}

def mutate(payload):
    with LOCK:
        old=digest()
        if payload.get('revision')!=old: raise ValueError('다른 변경이 있습니다. 새로고침 후 저장하세요.')
        w=openpyxl.load_workbook(FILE); all_assets=assets(w); aid=payload.get('id'); before={}
        if aid:
            before=next((a for a in all_assets if a['id']==aid),None)
            if not before: raise ValueError('자산이 없습니다.')
            division=before['division']; row=before['row']
            action=payload.get('action','')
            if action not in ('','수거','사용자 변경','사용자 확인'): raise ValueError('작업 유형이 올바르지 않습니다.')
            payload={**before,**payload}
            if action=='수거': payload.update(user='',uid='',status='보관')
            if action=='사용자 변경':
                if not str(payload.get('user','')).strip() and not str(payload.get('uid','')).strip(): raise ValueError('새 사용자를 입력하세요.')
                payload['status']='사용'
            if action=='사용자 확인' and not before['user']: raise ValueError('현재 배정된 사용자가 없습니다.')
        else:
            division=payload.get('division')
            if division not in CONFIG: raise ValueError('사업부를 선택하세요.')
            used=[a['row'] for a in all_assets if a['division']==division]; row=max(used,default=5)+1; aid=str(uuid.uuid4())
        name,date,status,grade,note=CONFIG[division]; s=w[name]
        number=str(payload.get('number','')).strip()
        if not number: raise ValueError('관리번호는 필수입니다.')
        if (not before or number!=before['number']) and any(a['number']==number and a['id']!=aid for a in all_assets): raise ValueError('동일 관리번호가 있습니다.')
        state=payload.get('status','보관')
        if state not in ['사용','보관','수리','폐기','분실']: raise ValueError('상태 값이 올바르지 않습니다.')
        datevalue=str(payload.get('date','')).strip()
        parsed=dt.date.fromisoformat(datevalue) if datevalue else None
        uid=str(payload.get('uid','')).strip(); username=str(payload.get('user','')).strip()
        if uid:
            matches=[r for r in w['앱_LDAP사용자'].iter_rows(min_row=2,values_only=True) if value(r[0])==uid]
            if len(matches)!=1: raise ValueError('LDAP 계정 선택이 올바르지 않습니다.')
            username=value(matches[0][1]) or uid
        if not before:
            for c in range(1,28):
                src=s.cell(row-1,c); dst=s.cell(row,c)
                if src.has_style: dst._style=copy.copy(src._style)
        for k,c in BASE.items(): literal(s[f'{c}{row}'],username if k=='user' else payload.get(k,''))
        literal(s[f'{note}{row}'],payload.get('note',''));literal(s[f'{status}{row}'],state)
        s[f'{date}{row}']=parsed; s[f'{date}{row}'].number_format='yyyy-mm-dd'
        s[f'{grade}{row}']=f'=IF({date}{row}="","",IF(DATEDIF({date}{row},TODAY(),"Y")>=10,"D",IF(DATEDIF({date}{row},TODAY(),"Y")>=5,"C",IF(DATEDIF({date}{row},TODAY(),"Y")>=3,"B","A"))))'
        s[f'AZ{row}']=aid;literal(s[f'BA{row}'],uid)
        after=next(a for a in assets(w) if a['id']==aid)
        w['앱_이력'].append([dt.datetime.now().astimezone().isoformat(),aid,payload.get('action') or ('수정' if before else '등록'),json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False)])
        save(w,old)
        return {'id':aid}

def qr_svg(url):
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderSVG
    qr=QrCodeWidget(url);x,y,x2,y2=qr.getBounds();d=Drawing(240,240,transform=[240/(x2-x),0,0,240/(y2-y),0,0]);d.add(qr)
    return renderSVG.drawToString(d).encode()

SESSION=secrets.token_urlsafe(32)
def application(env,start):
    def reply(code,data,ctype='application/json; charset=utf-8',extra=None):
        if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False).encode()
        start(code,[('Content-Type',ctype),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY')]+(extra or []));return [data]
    gateway=os.environ.get('BACKEND_GATEWAY_TOKEN','')
    if gateway and not hmac.compare_digest(env.get('HTTP_X_GATEWAY_TOKEN',''),gateway):
        return reply('403 Forbidden',{'error':'게이트웨이 인증이 필요합니다.'})
    password=os.environ.get('APP_PASSWORD','')
    auth=env.get('HTTP_AUTHORIZATION','')
    try: valid=auth.startswith('Basic ') and hmac.compare_digest(base64.b64decode(auth[6:]).decode(), 'admin:'+password)
    except Exception: valid=False
    if not password or not valid:return reply('401 Unauthorized',{'error':'관리자 로그인이 필요합니다.'},extra=[('WWW-Authenticate','Basic realm="Bumjin Assets"')])
    path=env.get('PATH_INFO','/'); method=env.get('REQUEST_METHOD','GET')
    try:
        if method=='GET' and path=='/api/data':return reply('200 OK',{**snapshot(),'csrf':SESSION})
        if method=='GET' and path=='/api/export':return reply('200 OK',FILE.read_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', [('Content-Disposition','attachment; filename="Bumjin_IT_assets.xlsx"')])
        if method=='GET' and path.startswith('/qr/'):
            aid=path.split('/')[-1]
            if not any(a['id']==aid for a in snapshot()['assets']):return reply('404 Not Found',{})
            url=os.environ.get('PUBLIC_BASE_URL','http://127.0.0.1:8080').rstrip('/')+'/a/'+aid
            return reply('200 OK',qr_svg(url),'image/svg+xml')
        if method=='POST' and path=='/api/assets':
            if not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),SESSION):return reply('403 Forbidden',{'error':'세션이 변경됐습니다. 새로고침하세요.'})
            length=int(env.get('CONTENT_LENGTH') or '0')
            if length>20000:return reply('413 Payload Too Large',{})
            return reply('200 OK',mutate(json.loads(env['wsgi.input'].read(length))))
        if method=='GET' and (path=='/' or path.startswith('/a/')):return reply('200 OK',(ROOT.parent/'public/index.html').read_bytes(),'text/html; charset=utf-8')
        return reply('404 Not Found',{})
    except (ValueError,KeyError) as e:return reply('409 Conflict',{'error':str(e)})
    except PermissionError:return reply('409 Conflict',{'error':'엑셀이 열려 있거나 파일 권한이 없습니다. 파일을 닫고 재시도하세요.'})
    except Exception:
        import traceback;traceback.print_exc();return reply('500 Internal Server Error',{'error':'처리 실패. 서버 로그를 확인하세요.'})

if __name__=='__main__':
    if len(os.environ.get('APP_PASSWORD',''))<12:raise SystemExit('APP_PASSWORD를 12자 이상으로 지정하세요.')
    if os.environ.get('BACKEND_GATEWAY_TOKEN') and len(os.environ['BACKEND_GATEWAY_TOKEN'])<24:raise SystemExit('게이트웨이 토큰은 24자 이상이어야 합니다.')
    lock=FILE.with_suffix('.app.lock')
    try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise SystemExit('실행 중인 앱이 있습니다. 비정상 종료였다면 실행 프로세스가 없는지 확인한 뒤 .app.lock 파일을 삭제하세요.')
    try:
        os.close(fd);initialize();print('Bumjin Assets: http://127.0.0.1:8080 (로그인: admin)')
        make_server(os.environ.get('HOST','127.0.0.1'),int(os.environ.get('PORT','8080')),application).serve_forever()
    finally:lock.unlink(missing_ok=True)
