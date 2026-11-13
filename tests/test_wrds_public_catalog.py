import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from wrds_catalog.catalog import build_catalog,validate_catalog,export_catalog,checked_payload

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.inventory=self.root/'inventory.json';self.seed=self.root/'seed.json';self.db=self.root/'catalog.sqlite'
        self.inventory.write_text(json.dumps({'metadata':{'as_of':'2026-11-14'},'artifacts':[{
            'artifact_id':'a1','name':'synthetic.csv','size_bytes':7,'sha256':'a'*64,
            'materialization_status':'PRESENT_HASH_VERIFIED','category':'SYNTHETIC',
            'classification_basis':'test fixture','publication':'METADATA_ONLY','source_channel':'fixture',
            'archive_members':None,'crc_status':'NOT_APPLICABLE'}]}))
        self.seed.write_text(json.dumps({'datasets':[{'dataset_id':'daily','title':'Synthetic daily',
            'unit':'logical row','description':'Artificial test input','admission_status':'NOT_ADMITTED'}]}))
    def tearDown(self):self.tmp.cleanup()
    def test_actual_build_export_and_reproducibility(self):
        a=build_catalog(self.inventory,self.seed,self.db)
        self.assertEqual(a['counts']['artifacts'],1)
        b=build_catalog(self.inventory,self.seed,self.root/'again.sqlite')
        self.assertEqual(a['sha256'],b['sha256'])
        export_catalog(self.db,self.root/'export')
        d=json.loads((self.root/'export/catalog.json').read_text())
        self.assertEqual(d['artifacts'][0]['name'],'synthetic.csv')
    def test_reject_private_paths_and_unknown_row_columns(self):
        for data in [{'metadata':{'path':'C:/Users/someone/secret'}},
                     {'artifacts':[{'name':'a.csv','raw_cells':'[123]'}]},
                     {'aggregate_results':[{'value':'real secret token'}]}]:
            with self.assertRaises(ValueError):checked_payload(data)
    def test_official_https_url_is_not_mistaken_for_drive_path(self):
        checked_payload({'official_sources':[{'source_id':'s','title':'Example',
          'url':'https://example.org/document','accessed_date':'2026-11-14','scope':'Test public link'}]})
    def test_foreign_key_failure_keeps_existing_database(self):
        build_catalog(self.inventory,self.seed,self.db);old=self.db.read_bytes()
        seed=json.loads(self.seed.read_text());seed['aggregate_results']=[{
            'result_id':'r','dataset_id':'absent','metric':'rows','value':2,'unit':'rows','as_of':'2026-11-14',
            'evidence_id':'missing','scope':'fixture','verification_status':'SYNTHETIC_TEST'}];self.seed.write_text(json.dumps(seed))
        with self.assertRaises(sqlite3.IntegrityError):build_catalog(self.inventory,self.seed,self.db)
        self.assertEqual(old,self.db.read_bytes())
    def test_independent_validation_rejects_tampered_payload(self):
        build_catalog(self.inventory,self.seed,self.db)
        c=sqlite3.connect(self.db);c.execute("UPDATE artifacts SET name='C:/private/data.csv'");c.commit();c.close()
        with self.assertRaises(ValueError):validate_catalog(self.db)
    def test_validation_rejects_extra_private_table(self):
        build_catalog(self.inventory,self.seed,self.db)
        c=sqlite3.connect(self.db);c.execute('CREATE TABLE private_rows(value TEXT)');c.commit();c.close()
        with self.assertRaises(ValueError):validate_catalog(self.db)

if __name__=='__main__':unittest.main()
