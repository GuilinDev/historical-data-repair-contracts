from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from resume_editing_ablation import safe_source_difference
from run_editing_ablation import source_difference


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.base=self.root/'bundle/packages/test/base';self.base.mkdir(parents=True)
        self.folder=self.root/'episode';self.candidate=self.folder/'candidate';self.candidate.mkdir(parents=True)
        self.data=SimpleNamespace(bundle=self.root/'bundle',tasks={'test':{'allowed':['a.py','new.py']}})
        self.row={'family':'test'}
        (self.base/'a.py').write_bytes(b'a = 1\n');(self.candidate/'a.py').write_bytes(b'a = 1\n')

    def test_new_authorized_file_counts_as_actual_source_change(self):
        (self.candidate/'new.py').write_text('class Compat:\n    pass\n',encoding='utf-8')
        result=safe_source_difference(self.data,self.row,self.folder)
        self.assertTrue(result['actual_source_change']);self.assertTrue(result['only_authorized_paths'])
        self.assertEqual(result['normalized_source_changed_paths'],['new.py'])

    def test_existing_files_agree_with_frozen_metadata_function(self):
        (self.candidate/'a.py').write_text('a = 2\n',encoding='utf-8')
        self.assertEqual(safe_source_difference(self.data,self.row,self.folder),source_difference(self.data,self.row,self.folder))

    def test_line_endings_alone_do_not_count(self):
        (self.candidate/'a.py').write_bytes(b'a = 1\r\n')
        result=safe_source_difference(self.data,self.row,self.folder)
        self.assertFalse(result['actual_source_change']);self.assertEqual(result['changed_paths'],['a.py'])

    def test_unauthorized_addition_is_not_source_acceptance(self):
        (self.candidate/'harness.py').write_text('x=1',encoding='utf-8')
        result=safe_source_difference(self.data,self.row,self.folder)
        self.assertFalse(result['only_authorized_paths']);self.assertFalse(result['actual_source_change'])


if __name__=='__main__':unittest.main()
