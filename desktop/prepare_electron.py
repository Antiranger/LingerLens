"""Provision a checksum-pinned Electron runtime through the system HTTPS stack."""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    dep = json.loads((ROOT / 'desktop/dependencies.json').read_text(encoding='utf-8'))['electron']
    package = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))
    if package['devDependencies']['electron'] != dep['version']:
        raise RuntimeError('Electron npm version and binary manifest must match')
    if sys.platform == 'darwin':
        arch = 'arm64' if platform.machine() == 'arm64' else 'x64'
        dep = {**dep, **dep['darwin'][arch]}
    build = ROOT / 'build-desktop'
    build.mkdir(exist_ok=True)
    archive = build / 'electron.zip'
    def digest(file):
        with file.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    if not archive.exists() or digest(archive) != dep['sha256']:
        print('Downloading pinned Electron runtime...', flush=True)
        temporary = build / 'electron.download'
        with urllib.request.urlopen(dep['url'], timeout=120) as source, temporary.open('wb') as target:
            shutil.copyfileobj(source, target)
        if digest(temporary) != dep['sha256']:
            raise RuntimeError('Electron SHA-256 mismatch')
        temporary.replace(archive)
    destination = build / 'electron'
    destination.mkdir(exist_ok=True)
    if sys.platform == 'darwin':
        # ditto preserves framework symlinks and executable permissions.
        subprocess.run(['/usr/bin/ditto', '-x', '-k', str(archive), str(destination)], check=True)
        return
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError('Unsafe Electron archive path')
        z.extractall(destination)
    print('Electron runtime ready.', flush=True)


if __name__ == '__main__':
    prepare()
