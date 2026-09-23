import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT=pathlib.Path(__file__).resolve().parents[3]/'scripts/provider-live-acceptance.py'

class ProviderLiveGuardTests(unittest.TestCase):
    def command(self,*extra):
        return subprocess.run([sys.executable,str(SCRIPT),'--config','does-not-exist.json',
            '--provider','fixture','--audio','does-not-exist.wav','--output','unused.json',*extra],
            capture_output=True,text=True,encoding='utf-8',timeout=10)
    def test_cost_consent_is_checked_before_any_file_or_cloud_access(self):
        result=self.command();self.assertEqual(result.returncode,2)
        self.assertIn('--confirm-paid is required',result.stderr)
        self.assertNotIn('Traceback',result.stderr)
    def test_duration_bound_precedes_cloud_access(self):
        result=self.command('--confirm-paid','--max-seconds','121')
        self.assertEqual(result.returncode,2);self.assertIn('must be 1..120',result.stderr)
    def test_existing_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            file=pathlib.Path(temp)/'result.json';file.write_text('keep',encoding='utf-8')
            result=self.command('--confirm-paid','--output',str(file))
            self.assertEqual(result.returncode,2);self.assertEqual(file.read_text(encoding='utf-8'),'keep')
if __name__=='__main__': unittest.main()
