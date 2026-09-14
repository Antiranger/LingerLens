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


def session_guard(token):
    from aiohttp import web

    @web.middleware
    async def guard(request, handler):
        # The private HLS route has its own random path token and is consumed
        # by FFmpeg. All UI/control routes require the desktop session token.
        if not request.path.startswith('/_private-hls/'):
            supplied = request.headers.get('X-LagLingo-Session', '')
            if not secrets.compare_digest(supplied, token):
                raise web.HTTPForbidden()
        return await handler(request)
    return guard


async def serve(data: Path, token: str, ready_output):
    from aiohttp import web
    from companion.server import CompanionApplication, errors

    media, providers = runtime_paths(data)
    providers.parent.mkdir(parents=True, exist_ok=True)
    args = argparse.Namespace(runtime_dir=media, providers_file=providers,
                              publish_delay=3.0, cookies_from_browser=None)
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
    except Exception:
        # Underlying provider exceptions can include secrets. The desktop shell
        # only needs a category; never forward raw exceptions into its UI/logs.
        print('LagLingo backend failed to start or stop.', file=sys.stderr)
        raise SystemExit(1)
