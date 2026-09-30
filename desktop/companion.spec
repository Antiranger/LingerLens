# PyInstaller spec: only application assets, never repository/runtime snapshots.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
app_root = root / 'prototype' / 'hls-companion'
media = root / 'build-desktop' / 'media'
yt_dlp = app_root / 'vendor' / 'yt-dlp'
# The licences travel WITH yt-dlp.exe. Shipping the executable alone would
# redistribute GPL-2.0-or-later mutagen, and several other libraries, with no
# notice and no text, which is the one thing this project must not do.
yt_dlp_notices = [name for name in ('LICENSE-yt-dlp.txt', 'THIRD_PARTY_LICENSES.txt')
                  if (yt_dlp / name).is_file()]
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if not python_license.exists():
    import sysconfig
    python_license = Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt'
datas = [(str(python_license), 'third-party/python'),
         (str(app_root / 'web-player'), 'web-player'),
         (str(app_root / 'companion' / 'data' / 'languages.json'), 'companion/data'),
         (str(media / 'notices'), 'third-party/ffmpeg')]
datas += [(str(yt_dlp / name), 'vendor/yt-dlp') for name in yt_dlp_notices]
suffix = '.exe' if sys.platform == 'win32' else ''
binaries = [(str(media / 'bin' / (name + suffix)), 'bin') for name in ('ffmpeg', 'ffprobe')]
if sys.platform == 'win32':
    binaries += [(str(yt_dlp / ('yt-dlp' + suffix)), 'vendor/yt-dlp')]
# macOS yt-dlp is a self-contained universal executable with an appended
# archive. prepare_macos copies it after COLLECT so architecture processing
# cannot discard its archive while thinning the executable.
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
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='lingerlens-backend',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='lingerlens-backend')
