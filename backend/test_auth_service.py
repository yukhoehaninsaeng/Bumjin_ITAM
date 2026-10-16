import io,json,os,tempfile,unittest
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch
import app,auth_service,admin_service

class LoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.original=app.FILE;app.FILE=Path(self.tmp.name)/'assets.xlsx'
        self.env=patch.dict(os.environ,{'APP_PASSWORD':'initial-password-123','PUBLIC_BASE_URL':'http://example.invalid','BACKEND_GATEWAY_TOKEN':''});self.env.start()
        auth_service.SESSIONS.clear();auth_service.PREAUTH.clear();auth_service.ATTEMPTS.clear()
    def tearDown(self):app.FILE=self.original;self.env.stop();self.tmp.cleanup()
    def request(self,path,method='GET',payload=None,cookie='',csrf='',**extra):
        captured={};body=json.dumps(payload).encode() if payload is not None else b''
        env={'PATH_INFO':path,'REQUEST_METHOD':method,'HTTP_COOKIE':cookie,'HTTP_X_CSRF_TOKEN':csrf,'CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body),'REMOTE_ADDR':'test-client',**extra}
        def start(code,headers):captured.update(code=code,headers=headers)
        result=b''.join(app.application(env,start));return captured['code'],captured['headers'],result
    def cookies(self,headers):
        pairs=[]
        for k,v in headers:
            if k=='Set-Cookie':c=SimpleCookie();c.load(v);pairs.extend(name+'='+m.value for name,m in c.items() if m.value)
        return '; '.join(pairs)
    def login(self,next='/'):
        _,headers,body=self.request('/api/auth/csrf');csrf=json.loads(body)['csrf'];cookie=self.cookies(headers)
        result=self.request('/api/auth/login','POST',{'username':'admin','password':'initial-password-123','next':next},cookie,csrf)
        self.assertEqual(result[0],'200 OK');return self.cookies(result[1]),result
    def test_html_redirect_api_no_challenge_and_public_login(self):
        status,headers,_=self.request('/manage/a/test-asset');self.assertEqual(status,'303 See Other');self.assertEqual(dict(headers)['Location'],'/login?next=%2Fmanage%2Fa%2Ftest-asset')
        status,headers,_=self.request('/api/data');self.assertEqual(status,'401 Unauthorized');self.assertNotIn('WWW-Authenticate',dict(headers))
        status,_,html=self.request('/login');self.assertEqual(status,'200 OK');self.assertIn(b'loginForm',html)
        status,_,svg=self.request('/login-logo.svg');self.assertEqual(status,'200 OK');self.assertIn(b'<svg',svg)
    def test_login_return_path_cookies_and_logout(self):
        cookie,result=self.login('/a/test-asset');self.assertEqual(json.loads(result[2])['next'],'/a/test-asset')
        header=next(v for k,v in result[1] if k=='Set-Cookie' and v.startswith(auth_service.COOKIE+'='));self.assertIn('HttpOnly',header);self.assertIn('SameSite=Lax',header);self.assertNotIn('Secure',header)
        status,_,body=self.request('/api/admin',cookie=cookie);self.assertEqual(status,'200 OK');csrf=json.loads(body)['csrf']
        status,_,_=self.request('/api/auth/logout','POST',{},cookie);self.assertEqual(status,'403 Forbidden')
        status,headers,_=self.request('/api/auth/logout','POST',{},cookie,csrf);self.assertEqual(status,'200 OK');self.assertIn('Max-Age=0',str(headers))
        self.assertEqual(self.request('/api/admin',cookie=cookie)[0],'401 Unauthorized')
    def test_login_requires_csrf_and_rejects_wrong_credentials(self):
        self.assertEqual(self.request('/api/auth/login','POST',{'username':'admin','password':'initial-password-123'})[0],'403 Forbidden')
        _,headers,body=self.request('/api/auth/csrf');cookie=self.cookies(headers);csrf=json.loads(body)['csrf']
        status,headers,body=self.request('/api/auth/login','POST',{'username':'admin','password':'wrong'},cookie,csrf)
        self.assertEqual(status,'401 Unauthorized');self.assertNotIn('WWW-Authenticate',dict(headers));self.assertNotIn('wrong',body.decode())
    def test_password_change_invalidates_sessions(self):
        cookie,_=self.login();admin_service.update_settings(app,{'operation':'password','current_password':'initial-password-123','new_password':'changed-password-456'})
        self.assertEqual(self.request('/api/admin',cookie=cookie)[0],'401 Unauthorized')
    def test_expiry_secure_cookie_and_safe_redirect(self):
        cookie,_=self.login('//outside.invalid');env={'HTTP_COOKIE':cookie};session=auth_service.current(app,env);session['expires']=0;self.assertIsNone(auth_service.current(app,env))
        for target in ('https://outside.invalid','//outside.invalid','/\\outside.invalid','/api/export','/login','//[','/a/x\r\nLocation: bad'):self.assertEqual(auth_service.safe_next(target),'/')
        with patch.dict(os.environ,{'PUBLIC_BASE_URL':'https://assets.example.invalid'}):self.assertIn('Secure',auth_service.cookie_header({},'test','value',30)[1])
    def test_session_csrf_is_bound_to_cookie(self):
        first,_=self.login();second,_=self.login()
        other=auth_service.current(app,{'HTTP_COOKIE':second})['csrf']
        self.assertEqual(self.request('/api/auth/logout','POST',{},first,other)[0],'403 Forbidden')
        self.assertEqual(self.request('/api/admin',cookie=first)[0],'200 OK')

    def test_throttle(self):
        _,headers,body=self.request('/api/auth/csrf');cookie=self.cookies(headers);csrf=json.loads(body)['csrf']
        for _ in range(8):self.assertEqual(self.request('/api/auth/login','POST',{'username':'admin','password':'wrong'},cookie,csrf)[0],'401 Unauthorized')
        self.assertEqual(self.request('/api/auth/login','POST',{'username':'admin','password':'wrong'},cookie,csrf)[0],'429 Too Many Requests')
    def test_unicode_password(self):
        with patch.dict(os.environ,{'APP_PASSWORD':'테스트비밀번호123456'}):self.assertTrue(admin_service.password_valid(app,'테스트비밀번호123456'))

if __name__=='__main__':unittest.main()

