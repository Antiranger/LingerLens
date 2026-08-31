#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from companion.providers import create_asr
from companion.providers.config import DEFAULT_CONFIG, resolve_secrets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Feed a 16-bit mono PCM WAV to a configured ASR provider and print its event timeline."
    )
    parser.add_argument("--audio", required=True, type=Path, help="Path to a 16-bit mono PCM WAV file")
    parser.add_argument("--provider-id", default="bailian-qwen3-realtime", help="ASR provider id from the built-in prototype config")
    parser.add_argument("--language", default="ja", help="Source language code (default: ja)")
    parser.add_argument("--chunk-ms", default=100, type=int, help="Audio chunk size in milliseconds (default: 100)")
    parser.add_argument("--no-realtime", action="store_true", help="Send WAV chunks without sleeping between them")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    config = resolve_secrets(DEFAULT_CONFIG)
    records = config["asr"]["providers"]
    record = next((item for item in records if item["id"] == args.provider_id), None)
    if record is None:
        raise SystemExit(f"unknown ASR provider id: {args.provider_id}")
    if not record.get("_apiKey"):
        raise SystemExit(f"{record.get('apiKeyEnv', 'API key environment variable')} is not set")

    with wave.open(str(args.audio), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise SystemExit("WAV must be 16-bit mono PCM")
        sample_rate = wav.getframerate()
        expected_rate = int(record.get("options", {}).get("sampleRate", 16000))
        if sample_rate != expected_rate:
            raise SystemExit(f"WAV sample rate must be {expected_rate} Hz (got {sample_rate})")
        frames_per_chunk = max(1, sample_rate * args.chunk_ms // 1000)
        chunks: list[bytes] = []
        while True:
            chunk = wav.readframes(frames_per_chunk)
            if not chunk:
                break
            chunks.append(chunk)

    provider = create_asr(record)
    stream = await provider.stream(language=args.language, hotwords=[], context=[])
    started = time.monotonic()

    async def print_events() -> None:
        async for event in stream:
            payload = {
                "localSeconds": round(time.monotonic() - started, 3),
                "type": event.type,
                "text": event.text,
                "stash": event.stash,
                "beginPcm": event.begin_pcm,
                "endPcm": event.end_pcm,
                "language": event.language,
                "message": event.message,
                "raw": event.raw,
            }
            print(json.dumps(payload, ensure_ascii=False), flush=True)

    reader = asyncio.create_task(print_events())
    try:
        for index, chunk in enumerate(chunks):
            await stream.push_pcm(chunk, index * args.chunk_ms / 1000.0)
            if not args.no_realtime:
                await asyncio.sleep(args.chunk_ms / 1000.0)
        await stream.flush()
        await asyncio.wait_for(reader, timeout=15)
    except asyncio.TimeoutError:
        print("event wait timed out after flush", file=sys.stderr)
    finally:
        await stream.aclose()
        if not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


def main() -> None:
    args = parse_args()
    if args.chunk_ms <= 0:
        raise SystemExit("--chunk-ms must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
