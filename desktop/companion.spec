# PyInstaller spec: only application assets, never repository/runtime snapshots.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
app_root = root / 'prototype' / 'hls-companion'
media = root / 'build-desktop' / 'media'
datas = [(str(Path(sys.base_prefix) / 'LICENSE.txt'), 'third-party/python'),
         (str(app_root / 'web-player'), 'web-player'),
         (str(app_root / 'companion' / 'data' / 'languages.json'), 'companion/data'),
         (str(app_root / 'runtime' / 'providers.example.json'), 'examples'),
         (str(media / 'notices'), 'third-party/ffmpeg')]
binaries = [(str(media / 'bin' / name), 'bin') for name in ('ffmpeg.exe', 'ffprobe.exe')]
binaries += [(str(app_root / 'vendor' / 'yt-dlp' / 'yt-dlp.exe'), 'vendor/yt-dlp')]
hiddenimports = []
for package in ('fugashi', 'unidic_lite'):
    extra_data, extra_bins, extra_imports = collect_all(package)
    datas += extra_data
    binaries += extra_bins
    hiddenimports += extra_imports
for package in ('aiohttp', 'langcodes', 'fugashi', 'unidic-lite'):
    datas += copy_metadata(package, recursive=True)

a = Analysis([str(root / 'desktop' / 'companion_entry.py')],
    pathex=[str(app_root), str(root / 'desktop')], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, excludes=['streamlink', 'playwright', 'pytest', 'tkinter'],
    noarchive=False)
pyz = PYZ(a.pure)
# langcodes' packaging hook includes a test README; it is not runtime data.
a.datas = [item for item in a.datas if not item[0].replace('\\', '/').startswith('langcodes/tests/')]
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='laglingo-backend',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='laglingo-backend')
