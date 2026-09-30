"""Verify resource seals in the app and the exact macOS distribution archives."""
from pathlib import Path
import argparse
import plistlib
import subprocess
import sys
import tempfile


def verify_app(app):
    with (app / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    if info.get('CFBundleIdentifier') != 'io.github.antiranger.lingerlens':
        raise RuntimeError('Unexpected application bundle identifier')
    subprocess.run(['codesign', '--verify', '--deep', '--strict', '--verbose=4', str(app)], check=True)
    print(f'Signature integrity verified: {app}', flush=True)


def verify_archives(directory):
    apps = list(directory.glob('mac*/LingerLens.app'))
    dmgs = list(directory.glob('LingerLens-*-macos-*.dmg'))
    zips = list(directory.glob('LingerLens-*-macos-*.zip'))
    if len(apps) != 1 or len(dmgs) != 1 or len(zips) != 1:
        raise RuntimeError('Expected one app, one DMG and one ZIP for this native build')
    verify_app(apps[0])
    with tempfile.TemporaryDirectory(prefix='lingerlens-signature-') as temporary:
        scratch = Path(temporary)
        unzipped = scratch / 'zip'
        unzipped.mkdir()
        subprocess.run(['ditto', '-x', '-k', str(zips[0]), str(unzipped)], check=True)
        verify_app(unzipped / 'LingerLens.app')
        mount = scratch / 'dmg'
        mount.mkdir()
        subprocess.run(['hdiutil', 'verify', str(dmgs[0])], check=True)
        subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(dmgs[0])], check=True)
        try:
            verify_app(mount / 'LingerLens.app')
        finally:
            subprocess.run(['hdiutil', 'detach', str(mount)], check=True)


def main():
    if sys.platform != 'darwin':
        raise RuntimeError('Run signature verification on native macOS')
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path)
    parser.add_argument('--release-dir', type=Path, default=Path('release'))
    args = parser.parse_args()
    if args.app:
        verify_app(args.app.resolve())
    else:
        verify_archives(args.release_dir.resolve())


if __name__ == '__main__':
    main()
