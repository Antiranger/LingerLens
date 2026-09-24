"""Proxy selection for outbound provider connections.

aiohttp ignores ``HTTP(S)_PROXY`` unless it is told otherwise, so the network
mode chosen in the UI (system / direct / manual, which ``server`` applies to
``os.environ``) never reached ASR or translation connections: they always went
direct. Where a service is only reachable through a proxy (Soniox from mainland
China, for example) that made recognition fail intermittently.

The environment is read at connect time, so a mode change applies to the next
connection. With no proxy configured this returns nothing and behaviour is the
same as before. Loopback and private addresses always go direct, because local
model gateways must never be sent through a proxy.
"""

from __future__ import annotations

import ipaddress
import logging
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

_warned_unsupported: set[str] = set()


def _is_local(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def proxy_for(url: str) -> str | None:
    """The HTTP proxy to use for ``url``, or ``None`` to connect directly."""
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if not host or _is_local(host):
        return None
    proxies = urllib.request.getproxies_environment()
    if proxies.get("no") and urllib.request.proxy_bypass_environment(host, proxies):
        return None
    secure = parsed.scheme in ("https", "wss")
    proxy = proxies.get("https" if secure else "http") or proxies.get("all")
    if not proxy:
        return None
    if urllib.parse.urlsplit(proxy).scheme not in ("http", "https"):
        # aiohttp only speaks HTTP CONNECT. Going direct is better than failing
        # every connection; say so once so the cause is visible in the log.
        if proxy not in _warned_unsupported:
            _warned_unsupported.add(proxy)
            log.warning("proxy %s is not an HTTP proxy; ASR/translation connections go direct", proxy)
        return None
    return proxy


def proxy_kwargs(url: str) -> dict[str, Any]:
    """``ws_connect``/``request`` keyword arguments carrying the proxy, if any."""
    proxy = proxy_for(url)
    return {"proxy": proxy} if proxy else {}
