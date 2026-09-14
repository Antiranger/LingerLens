"""Build-machine provisioning only. End users receive an offline installer."""
from pathlib import Path
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from prepare_electron import prepare as prepare_electron

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build-desktop'


def sha256(file):
    with file.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 11) or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Build with Windows x64 Python 3.11 in .venv-desktop.')
    BUILD.mkdir(exist_ok=True)
    prepare_electron()
    dep = json.loads((ROOT / 'desktop/dependencies.json').read_text(encoding='utf-8'))['ffmpeg']
    archive = BUILD / 'ffmpeg.zip'
    if not archive.exists() or sha256(archive) != dep['sha256']:
        temporary = BUILD / 'ffmpeg.download'
        print('Downloading pinned FFmpeg build (build machine only)...', flush=True)
        with urllib.request.urlopen(dep['url'], timeout=120) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        if sha256(temporary) != dep['sha256']:
            raise RuntimeError('FFmpeg SHA-256 mismatch; refusing to package.')
        temporary.replace(archive)
    media = BUILD / 'media'
    (media / 'bin').mkdir(parents=True, exist_ok=True)
    (media / 'notices').mkdir(exist_ok=True)
    # Extract specific filenames; never trust archive paths for writes.
    with zipfile.ZipFile(archive) as z:
        for name in ('ffmpeg.exe', 'ffprobe.exe'):
            matches = [p for p in z.namelist() if p.endswith('/bin/' + name)]
            if len(matches) != 1:
                raise RuntimeError('Unexpected FFmpeg archive layout')
            with z.open(matches[0]) as source, (media / 'bin' / name).open('wb') as target:
                shutil.copyfileobj(source, target)
        for name in ('LICENSE', 'README.txt'):
            matches = [p for p in z.namelist() if p.endswith('/' + name)]
            if len(matches) != 1:
                raise RuntimeError('Missing FFmpeg license or build provenance')
            (media / 'notices' / name).write_bytes(z.read(matches[0]))
    (media / 'notices' / 'SOURCE.json').write_text(json.dumps(dep, indent=2), encoding='utf-8')
    yt = ROOT / 'prototype/hls-companion/vendor/yt-dlp/yt-dlp.exe'
    expected = Path(str(yt) + '.sha256').read_text(encoding='utf-8').split()[0]
    if sha256(yt) != expected:
        raise RuntimeError('yt-dlp checksum mismatch')
    print('Building isolated Python runtime and application assets...', flush=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                    '--distpath', str(BUILD / 'backend'), '--workpath', str(BUILD / 'pyinstaller'),
                    str(ROOT / 'desktop/companion.spec')], cwd=ROOT, check=True)
    print('Backend ready. Run npm run desktop:pack or npm run desktop:dist.', flush=True)


if __name__ == '__main__':
    main()
