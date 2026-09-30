"""Native macOS build: pinned upstream media tools, no Homebrew runtime dependencies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from prepare_backend import BUILD, ROOT, sha256
from prepare_electron import prepare as prepare_electron
import urllib.request


def download(pin, destination):
    if destination.exists() and sha256(destination) == pin['sha256']:
        return
    partial = destination.with_suffix('.download')
    with urllib.request.urlopen(pin['url'], timeout=180) as source, partial.open('wb') as target:
        shutil.copyfileobj(source, target)
    if sha256(partial) != pin['sha256']:
        raise RuntimeError('Download checksum mismatch: ' + destination.name)
    partial.replace(destination)


def main():
    if sys.platform != 'darwin' or sys.version_info[:2] != (3, 11):
        raise RuntimeError('Run on macOS with native Python 3.11.')
    BUILD.mkdir(exist_ok=True)
    prepare_electron()
    pins = json.loads((ROOT / 'desktop/dependencies.json').read_text(encoding='utf-8'))
    pin = pins['ffmpeg-source']
    archive = BUILD / 'ffmpeg-source.tar.xz'
    download(pin, archive)
    source = BUILD / ('ffmpeg-' + pin['version'])
    if not source.exists():
        with tarfile.open(archive) as bundle:
            bundle.extractall(BUILD, filter='data')
    # No autodetected Homebrew libraries: only macOS system frameworks may link.
    configure = ['./configure', '--disable-autodetect', '--disable-doc',
                 '--disable-debug', '--disable-x86asm', '--enable-securetransport',
                 '--disable-ffplay', '--disable-shared', '--enable-static', '--enable-videotoolbox']
    subprocess.run(configure, cwd=source, check=True)
    subprocess.run(['make', '-j', str(os.cpu_count() or 2)], cwd=source, check=True)
    media = BUILD / 'media'
    (media / 'bin').mkdir(parents=True, exist_ok=True)
    notices = media / 'notices'
    notices.mkdir(exist_ok=True)
    for name in ('ffmpeg', 'ffprobe'):
        shutil.copy2(source / name, media / 'bin' / name)
    shutil.copy2(source / 'COPYING.LGPLv2.1', notices / 'LICENSE')
    (notices / 'SOURCE.json').write_text(json.dumps(pin, indent=2), encoding='utf-8')
    configuration = subprocess.run([str(media / 'bin/ffmpeg'), '-version'],
                                   capture_output=True, text=True, check=True)
    (notices / 'BUILD-CONFIGURATION.txt').write_text(configuration.stdout, encoding='utf-8')
    target = ROOT / 'prototype/hls-companion/vendor/yt-dlp/yt-dlp'
    download(pins['yt-dlp-macos'], target)
    target.chmod(0o755)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
                    '--distpath', str(BUILD / 'backend'), '--workpath', str(BUILD / 'pyinstaller'),
                    str(ROOT / 'desktop/companion.spec')], cwd=ROOT, check=True)
    bundled = BUILD / 'backend/lingerlens-backend/_internal/vendor/yt-dlp/yt-dlp'
    bundled.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, bundled)
    if sha256(bundled) != pins['yt-dlp-macos']['sha256']:
        raise RuntimeError('Bundled yt-dlp checksum mismatch')
    subprocess.run([str(bundled), '--version'], check=True)


if __name__ == '__main__':
    main()
