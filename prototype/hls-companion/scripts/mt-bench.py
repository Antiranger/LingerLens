#!/usr/bin/env python3
"""Measure translation latency: serial vs concurrent, on real ASR finals."""
from __future__ import annotations

import asyncio
import copy
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.providers import create_translation
from companion.providers.config import load_config
from companion.providers.base import StreamMeta, TranslationRequest

CFG = ROOT / "runtime" / "providers.json"
META = StreamMeta("ゲーム実況", "test", None, "ja", "zh")

SENTENCES = [
    "ええ。",
    "まあでも。",
    "終わりを見失った時はどうなることかと思ったけど。",
    "一時間半前が一番ピークだった。一時間半前の私はもうなんか。",
    "危険な状態だった。",
    "あまりにも。",
    "危険な状態だったな。",
    "今はむしろ。",
    "なんかすごい感動している。",
    "やっとこのゲームの終着点にたどり着くことができた。",
    "終わりがないってのが一番怖いからさ。",
    "すごくない？健全な状態で銃弾。",
]


def make_provider():
    config = load_config(CFG)
    record = copy.deepcopy(
        next(r for r in config["translation"]["providers"] if r["id"] == config["translation"]["active"])
    )
    record["baseUrl"] = record["baseUrl"].replace("https://127.0.0.1", "http://127.0.0.1")
    record.setdefault("options", {})["timeoutSeconds"] = 30
    return create_translation(record), record["model"]


async def one(provider, text, history):
    request = TranslationRequest(text, META, history, [], None)
    started = time.monotonic()
    try:
        result = await provider.translate(request)
        return round((time.monotonic() - started) * 1000), result.text
    except Exception as exc:  # noqa: BLE001
        return round((time.monotonic() - started) * 1000), f"<FAIL {type(exc).__name__}: {exc}>"


async def main():
    provider, model = make_provider()
    print(f"model={model}\n")

    print("=== SERIAL (current pipeline: 1 worker) ===")
    history: list[tuple[str, str]] = []
    serial: list[int] = []
    wall = time.monotonic()
    for text in SENTENCES:
        ms, out = await one(provider, text, history[-6:])
        serial.append(ms)
        if not out.startswith("<FAIL"):
            history.append((text, out))
        print(f"  {ms:6d}ms  {text}  ->  {out}")
    total_serial = time.monotonic() - wall
    print(f"  serial total={total_serial:.1f}s  p50={sorted(serial)[len(serial)//2]}ms  max={max(serial)}ms")

    print("\n=== CONCURRENT x4 (proposed) ===")
    wall = time.monotonic()
    results = await asyncio.gather(*(one(provider, t, []) for t in SENTENCES))
    total_par = time.monotonic() - wall
    lat = [ms for ms, _ in results]
    for (ms, out), text in zip(results, SENTENCES):
        print(f"  {ms:6d}ms  {text}  ->  {out}")
    print(f"  concurrent total={total_par:.1f}s  p50={sorted(lat)[len(lat)//2]}ms  max={max(lat)}ms")


asyncio.run(main())
