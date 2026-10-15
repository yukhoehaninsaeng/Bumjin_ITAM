import base64, io, json, os, shutil, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import app
import auth_service
import admin_service

class AdminTests(unittest.TestCase):
    def test_settings_directory_and_password(self):
        with tempfile.TemporaryDirectory() as directory:
            previous=app.FILE
            app.FILE=Path(directory)/'test.xlsx';shutil.copy2(previous,app.FILE)
            try:
                app.initialize();os.environ['APP_PASSWORD']='initial-password-123'
                admin_service.update_settings(app,{'operation':'ldap','ldap':{'host':'example.local','port':636},'bind_password':'secret-test'})
                self.assertNotIn('secret-test',admin_service.config_path(app).read_text())
                users=[['testuid','Test Person','test@example.invalid','unique-id','IT','uid=testuid,ou=IT,dc=example']]
                orgs=[{'name':'IT','dn':'ou=IT,dc=example'}]
                with patch.object(admin_service,'fetch_directory',return_value=(users,orgs)):
                    admin_service.synchronize(app,{'revision':app.digest()})
                snap=app.snapshot();self.assertEqual(snap['users'][0]['department'],'IT');self.assertEqual(snap['organizations'],orgs)
                aid=snap['assets'][0]['id']
                app.mutate({'id':aid,'action':'사용자 변경','uid':'testuid','revision':snap['revision']})
                self.assertEqual(next(a for a in app.snapshot()['assets'] if a['id']==aid)['uid'],'testuid')
                users[0][3]='changed-id';original=app.digest()
                with patch.object(admin_service,'fetch_directory',return_value=(users,orgs)):
                    with self.assertRaises(ValueError):admin_service.synchronize(app,{'revision':original})
                self.assertEqual(app.digest(),original)
                with self.assertRaises(ValueError):admin_service.update_settings(app,{'operation':'password','current_password':'wrong','new_password':'new-password-123'})
                old_token,_=auth_service.issue_session(app)
                admin_service.update_settings(app,{'operation':'password','current_password':'initial-password-123','new_password':'new-password-123'})
                self.assertTrue(admin_service.password_valid(app,'new-password-123'));self.assertFalse(admin_service.password_valid(app,'initial-password-123'))
                self.assertNotIn('new-password-123',admin_service.config_path(app).read_text())
                statuses=[]
                old=auth_service.COOKIE+'='+old_token
                app.application({'PATH_INFO':'/api/admin','REQUEST_METHOD':'GET','HTTP_COOKIE':old},lambda s,h:statuses.append(s))
                self.assertTrue(statuses[-1].startswith('401'))
                token,_=auth_service.issue_session(app);auth=auth_service.COOKIE+'='+token
                response=app.application({'PATH_INFO':'/api/admin','REQUEST_METHOD':'GET','HTTP_COOKIE':auth},lambda s,h:statuses.append(s))
                self.assertTrue(statuses[-1].startswith('200'));self.assertNotIn('password_hash',json.loads(response[0]))
                app.application({'PATH_INFO':'/api/admin','REQUEST_METHOD':'POST','HTTP_COOKIE':auth},lambda s,h:statuses.append(s))
                self.assertTrue(statuses[-1].startswith('403'))
            finally:app.FILE=previous;admin_service.LDAP_PASSWORD=''
if __name__=='__main__':unittest.main()
