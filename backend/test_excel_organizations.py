import os,shutil,tempfile,unittest
from pathlib import Path
import openpyxl
import app,organization_service as org

class ExcelOrganizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.original=app.FILE;app.FILE=Path(self.tmp.name)/'test.xlsx';shutil.copy2(os.environ['TEST_WORKBOOK'],app.FILE)
        w=openpyxl.load_workbook(app.FILE)
        for key in ('BJC','BJE'):
            app.literal(w[app.CONFIG[key][0]]['C6'],'Shared Department')
        app.literal(w[app.CONFIG['BJE:모니터'][0]]['C6'],'Research Circuit Team')
        w.save(app.FILE);app.initialize()
    def tearDown(self):app.FILE=self.original;self.tmp.cleanup()
    def test_initial_import_separates_corporations_and_preserves_names(self):
        snap=app.snapshot();items=snap['managed_organizations'];paths={org.path_for(items,o['id']):o['id'] for o in items}
        for path in ('BJC','BJE','BJM','BJC/Shared Department','BJE/Shared Department','BJE/Research Circuit Team'):self.assertIn(path,paths)
        self.assertNotEqual(paths['BJC/Shared Department'],paths['BJE/Shared Department'])
        self.assertEqual(len(snap['asset_organizations']),len(snap['assets']))
        self.assertEqual(len(paths),len(items));self.assertNotIn('mode',snap)
        monitor=next(a for a in snap['assets'] if a['division']=='BJE' and a['category']=='모니터' and a['row']==6)
        self.assertEqual(snap['asset_organizations'][monitor['id']],paths['BJE/Research Circuit Team'])
    def test_edit_keeps_asset_links_and_does_not_reimport_on_restart(self):
        before=app.snapshot();items=before['managed_organizations'];target=next(o for o in items if org.path_for(items,o['id'])=='BJE/Shared Department')
        parent=org.mutate(app,{'operation':'create','name':'Head Office','parent_id':target['parent_id'],'revision':before['revision']})['id']
        org.mutate(app,{'operation':'update','id':target['id'],'name':'Renamed Team','parent_id':parent,'revision':app.digest()})
        edited=app.snapshot();self.assertEqual(edited['assets'],before['assets']);self.assertEqual(edited['asset_organizations'],before['asset_organizations'])
        app.initialize();restarted=app.snapshot();paths=[org.path_for(restarted['managed_organizations'],o['id']) for o in restarted['managed_organizations']]
        self.assertIn('BJE/Head Office/Renamed Team',paths);self.assertNotIn('BJE/Shared Department',paths)
        self.assertEqual(restarted['revision'],edited['revision'])
        with self.assertRaises(ValueError):org.mutate(app,{'operation':'delete','id':target['id'],'revision':app.digest()})
    def test_existing_tree_is_preserved_when_importing(self):
        snap=app.snapshot();w=openpyxl.load_workbook(app.FILE);del w[org.IMPORT_STATE];del w[org.ASSET_ORGS]
        items=org.rows(w);items.append({'id':'custom-root','name':'Custom','parent_id':''});org.write_rows(app,w,items);app.save(w,app.digest());app.initialize()
        self.assertIn('custom-root',[o['id'] for o in app.snapshot()['managed_organizations']])

if __name__=='__main__':unittest.main()
