"""Launch the packaged app with a fresh profile and verify real synthetic playback."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
if sys.platform == 'win32':
    binary = root / 'release/win-unpacked/LingerLens.exe'
else:
    candidates = list((root / 'release').glob('mac*/LingerLens.app/Contents/MacOS/LingerLens'))
    if len(candidates) != 1:
        raise RuntimeError('Expected exactly one native macOS package')
    binary = candidates[0]
output = root / 'output'
output.mkdir(exist_ok=True)
profile = Path(tempfile.mkdtemp(prefix='packaged-smoke-', dir=output))
env = dict(os.environ, LINGERLENS_SMOKE_OUTPUT=str(profile))
subprocess.run([str(binary), '--smoke-test'], env=env, check=True, timeout=90,
               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
result = json.loads((profile / 'result.json').read_text(encoding='utf-8'))
if result.get('status') != 200 or result.get('syntheticPlaybackSeconds', 0) < 0.2:
    raise RuntimeError('Packaged playback acceptance failed')
print(json.dumps(result, indent=2))
