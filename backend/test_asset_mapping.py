import base64, io, json, os, shutil, tempfile, unittest
from pathlib import Path
import openpyxl
import app
import auth_service

class AssetMappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.original=app.FILE
        app.FILE=Path(self.tmp.name)/'test.xlsx';shutil.copy2(os.environ['TEST_WORKBOOK'],app.FILE);app.initialize()
        w=openpyxl.load_workbook(app.FILE);all_assets=app.assets(w)
        pc=[a for a in all_assets if a['category']=='PC'][:2];monitor=next(a for a in all_assets if a['category']=='모니터');self.selected=pc+[monitor]
        for a in self.selected:
            key=a['division']+(':모니터' if a['category']=='모니터' else '');s=w[app.CONFIG[key][0]];app.literal(s[f'D{a["row"]}'],'Legacy Person');app.literal(s[f'BA{a["row"]}'],'')
        s=w['앱_LDAP사용자'];s.delete_rows(1,s.max_row);s.append(['uid','표시이름','이메일','디렉터리ID','부서','DN','설명']);s.append(['person1','person1','person@example.invalid','identity-1','BJE/IT','uid=person1,dc=example','Actual Person'])
        app.save(w,app.digest());self.before=app.snapshot();self.payload={'source_name':'Legacy Person','target_directory_id':'identity-1','asset_ids':[a['id'] for a in self.selected],'revision':self.before['revision']}
    def tearDown(self):app.FILE=self.original;self.tmp.cleanup()
    def test_maps_all_categories_and_preserves_other_data(self):
        result=app.map_user_assets(self.payload);self.assertEqual(result['mapped'],3)
        after=app.snapshot();index={a['id']:a for a in after['assets']};selected=set(self.payload['asset_ids'])
        for before in self.before['assets']:
            now=index[before['id']]
            if before['id'] in selected:
                self.assertEqual((now['uid'],now['user']),('person1','Actual Person'))
                for k in before:
                    if k not in ('uid','user'):self.assertEqual(now[k],before[k],k)
            else:self.assertEqual(now,before)
        self.assertEqual(len([h for h in after['history'] if h[2]=='LDAP 일괄 매핑']),3)
    def test_rejects_stale_or_mixed_owners_atomically(self):
        original=app.digest();other=next(a for a in self.before['assets'] if a['id'] not in self.payload['asset_ids'])
        for payload in [{**self.payload,'revision':'old'},{**self.payload,'asset_ids':self.payload['asset_ids']+[other['id']]},{**self.payload,'asset_ids':self.payload['asset_ids']*2},{**self.payload,'target_directory_id':'missing'}]:
            with self.assertRaises(ValueError):app.map_user_assets(payload)
            self.assertEqual(app.digest(),original)
    def test_can_map_subset_and_reject_already_mapped(self):
        first=self.payload['asset_ids'][0];app.map_user_assets({**self.payload,'asset_ids':[first]});snap=app.snapshot();self.assertEqual(sum(a['uid']=='person1' for a in snap['assets']),1)
        original=app.digest()
        with self.assertRaises(ValueError):app.map_user_assets({**self.payload,'revision':original})
        self.assertEqual(app.digest(),original)
    def test_endpoint_auth_csrf_and_success(self):
        os.environ['APP_PASSWORD']='mapping-test-password';token,session=auth_service.issue_session(app);auth=auth_service.COOKIE+'='+token;statuses=[]
        env={'PATH_INFO':'/api/assets/map-user','REQUEST_METHOD':'POST','HTTP_COOKIE':auth}
        app.application(env,lambda s,h:statuses.append(s));self.assertTrue(statuses[-1].startswith('403'))
        body=json.dumps(self.payload).encode();out=app.application({**env,'HTTP_X_CSRF_TOKEN':session['csrf'],'CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body)},lambda s,h:statuses.append(s));self.assertTrue(statuses[-1].startswith('200'));self.assertEqual(json.loads(out[0])['mapped'],3)

if __name__=='__main__':unittest.main()
