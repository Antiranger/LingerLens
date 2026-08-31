#!/usr/bin/env python3
"""Probe the configured translation provider exactly the way the pipeline does.

Never prints the API key. Prints only status, error type and message.
"""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers import create_translation
from companion.providers.config import load_config
from companion.providers.base import StreamMeta, TranslationRequest

CFG = ROOT / "runtime" / "providers.json"


async def try_provider(record: dict, label: str) -> None:
    provider = create_translation(record)
    request = TranslationRequest(
        source_text="これぐらいじゃなくっちゃ。",
        meta=StreamMeta("test", "test", None, "ja", "zh"),
        history=[],
        glossary=[],
        deadline_monotonic=None,
    )
    try:
        result = await asyncio.wait_for(provider.translate(request), timeout=20)
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] FAIL {type(exc).__name__}: {exc}")
        cause = exc.__cause__ or exc.__context__
        if cause is not None:
            print(f"[{label}]   cause {type(cause).__name__}: {cause}")
        return
    print(f"[{label}] OK {result.latency_ms}ms -> {result.text!r}")


async def main() -> None:
    config = load_config(CFG)
    for record in config["translation"]["providers"]:
        label = f"{record['id']}@{record['baseUrl']}"
        print(f"--- {label} (key configured: {bool(record.get('_apiKey'))}) ---")
        await try_provider(record, label)

    # Same active provider, but with the scheme corrected to http.
    active_id = config["translation"]["active"]
    active = next(r for r in config["translation"]["providers"] if r["id"] == active_id)
    if active["baseUrl"].startswith("https://127.0.0.1"):
        patched = copy.deepcopy(active)
        patched["baseUrl"] = active["baseUrl"].replace("https://", "http://", 1)
        print(f"--- PATCHED to {patched['baseUrl']} ---")
        await try_provider(patched, "patched-http")


asyncio.run(main())
