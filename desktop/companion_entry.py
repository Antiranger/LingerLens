"""Private desktop entry. Startup/shutdown use stdin; no secrets in argv or logs."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading

from windows_job import own_process_tree

RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1] / 'prototype/hls-companion'))
sys.path.insert(0, str(RESOURCE_ROOT))


def runtime_paths(data: Path):
    return data / 'runtime' / 'media', data / 'runtime' / 'providers.json'


def backend_arguments(media: Path, providers: Path, port: int = 0) -> argparse.Namespace:
    """Arguments the embedded backend runs with.

    `host` and `port` are not decoration: the subtitle pipeline rebuilds the
    private HLS URL it reads the video timeline from
    (`CompanionApplication._private_hls_url`). Any stream the backend has to
    repackage first -- Bilibili live, for one -- goes through that method, and
    without these fields the ASR start raised
    `AttributeError: 'Namespace' object has no attribute 'host'`, so the whole
    subtitle pipeline failed to start and no caption was ever produced.

    Only the loopback address is ever served, and the port is dynamic, so the
    real value is written back once the socket is bound.
    """
    return argparse.Namespace(runtime_dir=media, providers_file=providers,
                              publish_delay=3.0, cookies_from_browser=None,
                              host='127.0.0.1', port=port)


def session_guard(token):
    from aiohttp import web

    @web.middleware
    async def guard(request, handler):
        # The private HLS route has its own random path token and is consumed
        # by FFmpeg. All UI/control routes require the desktop session token.
        if not request.path.startswith('/_private-hls/'):
            supplied = request.headers.get('X-LingerLens-Session', '')
            if not secrets.compare_digest(supplied, token):
                raise web.HTTPForbidden()
        return await handler(request)
    return guard


async def serve(data: Path, token: str, ready_output):
    from aiohttp import web
    from companion.server import CompanionApplication, errors
    from companion.providers.config import DEFAULT_CONFIG, atomic_write_config
    import copy

    media, providers = runtime_paths(data)
    providers.parent.mkdir(parents=True, exist_ok=True)
    # A distributed desktop app starts without configured accounts or models.
    # Existing user profiles belong to their owner and survive upgrades.
    if not providers.exists():
        blank = copy.deepcopy(DEFAULT_CONFIG)
        blank['asr'] = {'active': None, 'providers': []}
        blank['translation'] = {'active': None, 'fallback': [], 'providers': []}
        blank['chatTranslation'] = {'active': None}
        atomic_write_config(providers, blank)
    args = backend_arguments(media, providers)
    companion = CompanionApplication(args, enable_native_control=False)
    application = companion.routes()
    application.middlewares.extend([session_guard(token), errors])
    runner = web.AppRunner(application)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()

    def read_parent():
        # EOF also means Electron disappeared. Never keep charging in background.
        for line in sys.stdin:
            if line.strip() == 'stop':
                break
        loop.call_soon_threadsafe(stopping.set)

    try:
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = runner.addresses[0][1]
        # The pipeline builds the private HLS URL it reads video from, so it has
        # to name the port this socket actually bound.
        args.port = port
        print(json.dumps({'event': 'ready', 'port': port, 'pid': os.getpid()}), file=ready_output, flush=True)
        threading.Thread(target=read_parent, daemon=True, name='desktop-parent').start()
        await stopping.wait()
    finally:
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--check-dependencies', action='store_true')
    args = parser.parse_args()
    if getattr(sys, 'frozen', False):
        os.environ['PATH'] = str(RESOURCE_ROOT / 'bin') + os.pathsep + os.environ.get('PATH', '')
    if args.check_dependencies:
        from companion.clause_boundaries import _tagger
        from companion.languages import catalog_entries
        from companion.providers import REGISTRY
        from companion.core import YtDlpProbe, executable
        for command in ([executable('ffmpeg'), '-version'], [executable('ffprobe'), '-version'], [YtDlpProbe().yt_dlp, '--version']):
            subprocess.run(command, check=True, capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        print(json.dumps({'japaneseTokens': len(list(_tagger()('今日は良い天気です。'))),
                          'providerKinds': len(REGISTRY), 'languages': len(catalog_entries())}))
        return 0
    if args.data_dir is None:
        parser.error('--data-dir is required')
    message = json.loads(sys.stdin.readline())
    token = message.get('token', '')
    if not isinstance(token, str) or len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid desktop session')
    proxy = message.get('proxy')
    if isinstance(proxy, str) and proxy.startswith(('http://', 'https://', 'socks5://')):
        # Explorer-launched Electron does not necessarily inherit the user's
        # terminal proxy environment. Forward the resolved Windows proxy to
        # yt-dlp and FFmpeg while keeping loopback traffic direct.
        for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            os.environ[name] = proxy
        os.environ['NO_PROXY'] = '127.0.0.1,localhost,::1'
    job = own_process_tree()
    if os.name == 'nt':
        # Every existing media subprocess inherits hidden-window startup settings.
        class HiddenStartup(subprocess.STARTUPINFO):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                self.wShowWindow = subprocess.SW_HIDE
        subprocess.STARTUPINFO = HiddenStartup
    ready_output = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        asyncio.run(serve(args.data_dir.resolve(), token, ready_output))
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except Exception as error:
        # Underlying provider exceptions can include secrets. The desktop shell
        # only needs a category; never forward raw exceptions into its UI/logs.
        if '--check-dependencies' in sys.argv:
            # Build-time inventory contains no provider requests or user profile.
            print(f'Dependency check failed: {type(error).__name__}: {error}', file=sys.stderr)
            if isinstance(error, subprocess.CalledProcessError):
                detail = error.stderr or b''
                print(detail.decode('utf-8', errors='replace') if isinstance(detail, bytes) else detail, file=sys.stderr)
        else:
            print('LingerLens backend failed to start or stop.', file=sys.stderr)
        raise SystemExit(1)
