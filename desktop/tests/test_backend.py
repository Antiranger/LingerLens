"""Offline desktop process contract, authentication, persistence and teardown."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lingerlens-desktop-test-')
        self.data = Path(self.temp.name)
        self.process = None

    def launch(self):
        binary = os.environ.get('LINGERLENS_TEST_BACKEND')
        command = [binary] if binary else [sys.executable, str(ROOT / 'desktop/companion_entry.py')]
        env = dict(os.environ, PYTHONUTF8='1')
        if binary:
            env['PATH'] = os.path.join(os.environ['SystemRoot'], 'System32') if os.name == 'nt' else '/usr/bin:/bin'
        self.process = subprocess.Popen(command + ['--data-dir', str(self.data)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.process.stdin.write(json.dumps({'token': 'a' * 64}) + '\n')
        self.process.stdin.flush()
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(self.process.stdout.readline()), daemon=True).start()
        line = lines.get(timeout=30)
        self.assertTrue(line, 'Backend exited before ready')
        message = json.loads(line)
        self.assertGreater(message['pid'], 0)
        self.origin = 'http://127.0.0.1:' + str(message['port'])

    def request(self, path, token=True):
        headers = {'X-LingerLens-Session': 'a' * 64} if token else {}
        return urllib.request.urlopen(urllib.request.Request(self.origin + path, headers=headers), timeout=5)

    def stop(self, eof=False):
        if not eof:
            self.process.stdin.write('stop\n')
        self.process.stdin.close()
        self.assertEqual(self.process.wait(timeout=10), 0)
        self.process.stdout.close()
        self.process.stderr.close()

    def tearDown(self):
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        if self.process:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
        self.temp.cleanup()

    def test_status_assets_and_authentication(self):
        self.launch()
        for path in ('/', '/api/status', '/api/languages', '/api/model-settings', '/player.js', '/vendor/hls.min.js'):
            with self.request(path) as response:
                self.assertEqual(response.status, 200, path)
            with self.assertRaises(urllib.error.HTTPError) as denied:
                self.request(path, token=False)
            self.assertEqual(denied.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as denied:
            self.request('/_private-hls/incorrect/index.m3u8', token=False)
        self.assertEqual(denied.exception.code, 404)
        self.stop()

    def test_eof_stops_backend_and_restart_preserves_config(self):
        self.launch()
        config = self.data / 'runtime' / 'providers.json'
        with self.request('/api/model-settings'):
            pass
        before = config.read_bytes()
        self.stop(eof=True)
        self.launch()
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse((self.data / 'runtime' / 'control.secret').exists())
        self.stop()

    def test_fresh_profile_has_no_configured_models_or_cookies(self):
        self.launch()
        with self.request('/api/model-settings') as response:
            settings = json.load(response)
        for section in ('asr', 'translation'):
            self.assertEqual(settings[section]['providers'], [])
            self.assertIsNone(settings[section]['active'])
        self.assertIsNone(settings['chatTranslation']['active'])
        self.assertFalse((self.data / 'runtime' / 'auth-snapshot.json').exists())
        with self.request('/api/languages') as response:
            languages = json.load(response)
        self.assertTrue(languages['languages'])
        self.assertIsNone(languages['asr'])
        self.stop()

    def test_first_native_model_can_be_saved_without_a_translation_model(self):
        self.launch()
        with self.request('/api/model-settings') as response:
            settings = json.load(response)
        settings['asr'] = {'active': 'own-soniox', 'providers': [{
            'id': 'own-soniox', 'label': 'Test connection', 'kind': 'soniox-realtime',
            'model': 'stt-rt-v5', 'baseUrl': 'wss://stt-rt.soniox.com/transcribe-websocket',
            'apiKey': 'offline-test-key', 'options': {'translationType': 'one_way'},
        }]}
        request = urllib.request.Request(self.origin + '/api/model-settings',
            data=json.dumps(settings).encode('utf-8'), method='POST',
            headers={'X-LingerLens-Session': 'a' * 64, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            saved = json.load(response)
        self.assertEqual(saved['translation']['providers'], [])
        with self.request('/api/languages') as response:
            languages = json.load(response)
        self.assertEqual(languages['asr']['providerId'], 'own-soniox')
        self.assertTrue(languages['translation']['native'])
        self.stop()

    @unittest.skipUnless(os.environ.get('LINGERLENS_TEST_BACKEND'), 'packaged dependency check')
    def test_packaged_dependencies_without_system_tools(self):
        env = dict(os.environ, PATH=os.path.join(os.environ['SystemRoot'], 'System32') if os.name == 'nt' else '/usr/bin:/bin')
        result = subprocess.run([os.environ['LINGERLENS_TEST_BACKEND'], '--check-dependencies'],
            capture_output=True, text=True, encoding='utf-8', env=env, timeout=45,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertGreater(status['japaneseTokens'], 0)
        self.assertGreater(status['providerKinds'], 0)
        self.assertGreater(status['languages'], 0)


if __name__ == '__main__':
    unittest.main()
