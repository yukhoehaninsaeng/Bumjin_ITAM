import base64, io, json, os, shutil, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import ldap3
import app, admin_service, organization_service as org

class FakeLDAP:
    def __init__(self, fail=0):
        self.result={'result':0};self.response=[];self.calls=[];self.fail=fail;self.closed=False;self.current={'stable-1':'','stable-2':''}
    def search(self,base,filt,attributes):
        stable=next(i for i in self.current if i in filt);uid='user'+stable[-1]
        self.response=[{'type':'searchResEntry','dn':'uid='+uid+',cn=users,dc=example','attributes':{'uid':[uid],'entryUUID':[stable],'departmentNumber':[self.current[stable]] if self.current[stable] else []}}];self.result={'result':0};return True
    def modify(self,dn,changes):
        self.calls.append((dn,changes))
        if len(self.calls)==self.fail:self.result={'result':50,'description':'insufficientAccessRights'};return False
        self.result={'result':0};return True
    def unbind(self):self.closed=True

class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.previous=app.FILE;app.FILE=Path(self.temp.name)/'test.xlsx'
        shutil.copy2(os.environ['TEST_WORKBOOK'],app.FILE);app.initialize()
        admin_service.LDAP_PASSWORD='test-only'
        admin_service.update_settings(app,{'operation':'ldap','ldap':{'host':'example','port':636,'base_dn':'dc=example','bind_dn':'uid=writer,dc=example','department_attr':'departmentNumber','allow_department_write':True}})
        self.users=[['user1','user1','u1@example.invalid','stable-1','','uid=user1,cn=users,dc=example','Person One'],['user2','user2','u2@example.invalid','stable-2','','uid=user2,cn=users,dc=example','Person Two']]
        with patch.object(admin_service,'fetch_directory',return_value=(self.users,[])):admin_service.synchronize(app,{'revision':app.digest()})
    def tearDown(self):app.FILE=self.previous;admin_service.LDAP_PASSWORD='';self.temp.cleanup()
    def mutate(self,**p):return org.mutate(app,{**p,'revision':app.digest()})
    def create_tree(self):
        root=self.mutate(operation='create',name='BJE')['id'];return self.mutate(operation='create',name='IT',parent_id=root)['id']
    def test_description_and_sync_preserves_tree(self):
        self.assertEqual(app.snapshot()['users'][0]['description'],'Person One');oid=self.create_tree()
        with patch.object(admin_service,'fetch_directory',return_value=(self.users,[])):admin_service.synchronize(app,{'revision':app.digest()})
        self.assertIn(oid,[o['id'] for o in app.snapshot()['managed_organizations']])
    def test_preview_and_scoped_write(self):
        oid=self.create_tree();conn=FakeLDAP()
        with patch.object(admin_service,'connect_directory',return_value=conn):
            p=self.mutate(operation='preview',id=oid,user_ids=['stable-1']);self.assertEqual(p['department'],'BJE/IT');self.assertFalse(conn.calls)
            result=self.mutate(operation='assign',id=oid,user_ids=['stable-1'])
        self.assertEqual(result['results'][0]['status'],'success');self.assertEqual(conn.calls[0][1],{'departmentNumber':[(ldap3.MODIFY_REPLACE,['BJE/IT'])]});self.assertTrue(conn.closed)
        snap=app.snapshot();self.assertEqual(snap['users'][0]['department'],'BJE/IT');self.assertEqual(snap['users'][1]['department'],'')
        audit=(app.FILE.parent/'ldap_department_audit.jsonl').read_text();self.assertNotIn('test-only',audit)
        with self.assertRaises(ValueError):self.mutate(operation='update',id=oid,name='New IT',parent_id=snap['managed_organizations'][0]['id'])
        with self.assertRaises(ValueError):self.mutate(operation='delete',id=oid)
    def test_partial_failure_and_disable(self):
        oid=self.create_tree();conn=FakeLDAP(fail=2)
        with patch.object(admin_service,'connect_directory',return_value=conn):result=self.mutate(operation='assign',id=oid,user_ids=['stable-1','stable-2'])
        self.assertEqual([r['status'] for r in result['results']],['success','failed']);self.assertEqual(app.snapshot()['users'][0]['department'],'BJE/IT');self.assertEqual(app.snapshot()['users'][1]['department'],'')
        admin_service.update_settings(app,{'operation':'ldap','ldap':{'allow_department_write':False}})
        with self.assertRaises(ValueError):self.mutate(operation='assign',id=oid,user_ids=['stable-2'])
    def test_lost_ldap_response_is_unknown(self):
        oid=self.create_tree();conn=FakeLDAP()
        with patch.object(admin_service,'connect_directory',return_value=conn),patch.object(conn,'modify',side_effect=ldap3.core.exceptions.LDAPSocketReceiveError('connection lost')):
            result=self.mutate(operation='assign',id=oid,user_ids=['stable-1'])
        self.assertEqual(result['results'][0]['status'],'unknown');self.assertEqual(app.snapshot()['users'][0]['department'],'')

    def test_external_change_preflight(self):
        oid=self.create_tree();conn=FakeLDAP();conn.current['stable-2']='External'
        with patch.object(admin_service,'connect_directory',return_value=conn):
            with self.assertRaises(ValueError):self.mutate(operation='assign',id=oid,user_ids=['stable-1','stable-2'])
        self.assertFalse(conn.calls)
    def test_cycle_duplicate_revision_and_guard(self):
        oid=self.create_tree();root=app.snapshot()['managed_organizations'][0]['id']
        for p in [dict(operation='update',id=root,name='BJE',parent_id=oid),dict(operation='create',name='IT',parent_id=root),dict(operation='create',name='bad/name')]:
            with self.assertRaises(ValueError):self.mutate(**p)
        with self.assertRaises(ValueError):org.mutate(app,{'operation':'create','name':'X','revision':'stale'})
        with self.assertRaises(ValueError):admin_service.update_settings(app,{'operation':'ldap','ldap':{'department_attr':'userPassword','allow_department_write':True}})
    def test_http_csrf(self):
        os.environ['APP_PASSWORD']='test-admin-password';auth='Basic '+base64.b64encode(b'admin:test-admin-password').decode();statuses=[]
        app.application({'PATH_INFO':'/api/organizations','REQUEST_METHOD':'POST','HTTP_AUTHORIZATION':auth},lambda s,h:statuses.append(s));self.assertTrue(statuses[-1].startswith('403'))
    def test_import_paths(self):
        w=__import__('openpyxl').load_workbook(app.FILE);org.import_paths(app,w,['BJE/IT/Support','BJE/IT/Support']);items=org.rows(w);self.assertEqual(len(items),3);self.assertEqual(org.path_for(items,items[-1]['id']),'BJE/IT/Support')
    def test_fetch_reads_description_and_department(self):
        from types import SimpleNamespace
        conn=FakeLDAP()
        responses=[[{'type':'searchResEntry','dn':'uid=user1,dc=example','attributes':{'uid':['user1'],'description':['Actual Person'],'entryUUID':['stable-1'],'mail':['person@example.invalid'],'departmentNumber':['BJE/IT']}}],[]]
        conn.extend=SimpleNamespace(standard=SimpleNamespace(paged_search=lambda *args,**kw:iter(responses.pop(0))))
        with patch.object(admin_service,'connect_directory',return_value=conn):users,orgs=admin_service.fetch_directory(app)
        self.assertEqual(users[0][6],'Actual Person');self.assertEqual(users[0][4],'BJE/IT');self.assertEqual(orgs,[]);self.assertTrue(conn.closed)

    def test_tls_choice_and_description_fetch(self):
        admin_service.update_settings(app,{'operation':'ldap','ldap':{'verify_tls':False,'ca_file':'/missing.pem'}})
        with patch('ldap3.Tls') as tls,patch('ldap3.Server'),patch('ldap3.Connection'):
            admin_service.connect_directory(app);self.assertEqual(tls.call_args.kwargs['validate'],__import__('ssl').CERT_NONE);self.assertIsNone(tls.call_args.kwargs['ca_certs_file'])

if __name__=='__main__':unittest.main()
