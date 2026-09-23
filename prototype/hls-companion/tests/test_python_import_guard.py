"""A disposable repository proves ignored runtime imports cannot pass the guard."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[3]/'scripts/check-python-imports.py'
spec=importlib.util.spec_from_file_location('python_import_guard',SCRIPT)
guard=importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)

class PythonImportGuardTests(unittest.TestCase):
    def test_untracked_import_fails_even_when_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); package=root/'prototype/hls-companion/companion'
            package.mkdir(parents=True)
            (package/'__init__.py').write_text('from . import native',encoding='utf-8')
            (package/'native.py').write_text('VALUE=1',encoding='utf-8')
            (root/'.gitignore').write_text('native.py',encoding='utf-8')
            subprocess.run(['git','init','-q',str(root)],check=True,capture_output=True)
            subprocess.run(['git','-C',str(root),'add','--','.gitignore','prototype/hls-companion/companion/__init__.py'],check=True,capture_output=True)
            self.assertTrue(any('native.py' in error for error in guard.check(root)))
            subprocess.run(['git','-C',str(root),'add','-f','--','prototype/hls-companion/companion/native.py'],check=True,capture_output=True)
            self.assertEqual(guard.check(root),[])
            (package/'native.py').unlink()
            (package/'__init__.py').write_text('from .native import VALUE',encoding='utf-8')
            self.assertTrue(any('missing' in error for error in guard.check(root)))

if __name__=='__main__': unittest.main()
