#!/usr/bin/env python3
"""Feed a real 16k mono WAV to Qwen realtime and dump the RAW event timeline.

Answers:
  1. Does conversation.item.input_audio_transcription.completed carry any
     timing fields at all, and under which keys?
  2. What is the full event vocabulary (are there events we drop in _map_event)?
  3. Does the transcript cover the whole clip (speech coverage vs. wall time)?
  4. Does a mid-speech input_audio_buffer.commit produce a final?

Never prints the API key.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
import uuid
import wave
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.providers.config import load_config  # noqa: E402

CFG = ROOT / "runtime" / "providers.json"
CHUNK_MS = 100


def load_pcm(path: Path) -> list[bytes]:
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getframerate() == 16000
        frames_per_chunk = 16000 * CHUNK_MS // 1000
        chunks = []
        while True:
            chunk = wav.readframes(frames_per_chunk)
            if not chunk:
                break
            chunks.append(chunk)
    return chunks


async def run(wav_path: Path, silence_ms: int, commit_every: float | None, label: str) -> None:
    config = load_config(CFG)
    record = next(r for r in config["asr"]["providers"] if r["kind"] == "dashscope-qwen-realtime")
    api_key = record["_apiKey"]
    if not api_key:
        raise SystemExit("no ASR api key configured")

    chunks = load_pcm(wav_path)
    total = len(chunks) * CHUNK_MS / 1000.0
    print(f"\n######## {label}  silence_ms={silence_ms} commit_every={commit_every} clip={total:.1f}s ########")

    url = f"{record['baseUrl']}?model={record['model']}"
    async with aiohttp.ClientSession() as session:
        ws = await session.ws_connect(url, headers={"Authorization": f"Bearer {api_key}"})
        await ws.send_json({
            "event_id": f"event_{uuid.uuid4().hex}",
            "type": "session.update",
            "session": {
                "input_audio_format": "pcm",
                "sample_rate": 16000,
                "input_audio_transcription": {"language": "ja"},
                "turn_detection": {"type": "server_vad", "threshold": 0.2, "silence_duration_ms": silence_ms},
            },
        })

        started = time.monotonic()
        sent_seconds = 0.0
        events: list[dict] = []
        event_types: dict[str, int] = {}
        finals: list[dict] = []
        done = asyncio.Event()

        async def reader() -> None:
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                raw = json.loads(message.data)
                etype = raw.get("type", "?")
                event_types[etype] = event_types.get(etype, 0) + 1
                rec = {"t": round(time.monotonic() - started, 3), "sent": round(sent_seconds, 2), "raw": raw}
                events.append(rec)
                if etype.endswith("speech_started") or etype.endswith("speech_stopped"):
                    print(f"  [{rec['t']:7.3f}] sent={rec['sent']:6.2f} {etype}  keys={sorted(k for k in raw if k != 'type')}")
                elif etype.endswith("transcription.completed"):
                    finals.append(rec)
                    other = {k: v for k, v in raw.items() if k not in ("type", "transcript", "event_id")}
                    print(f"  [{rec['t']:7.3f}] sent={rec['sent']:6.2f} FINAL {raw.get('transcript','')!r}")
                    print(f"            extra fields: {json.dumps(other, ensure_ascii=False)[:400]}")
                elif etype.endswith("transcription.text"):
                    pass  # interim, high volume
                else:
                    print(f"  [{rec['t']:7.3f}] {etype}: {json.dumps(raw, ensure_ascii=False)[:300]}")
                if etype == "session.finished":
                    done.set()

        reader_task = asyncio.create_task(reader())
        last_commit = 0.0
        for chunk in chunks:
            await ws.send_json({"type": "input_audio_buffer.append", "audio": base64.b64encode(chunk).decode("ascii")})
            sent_seconds += CHUNK_MS / 1000.0
            if commit_every and sent_seconds - last_commit >= commit_every:
                last_commit = sent_seconds
                await ws.send_json({"type": "input_audio_buffer.commit"})
                print(f"  [{time.monotonic()-started:7.3f}] --> manual commit at sent={sent_seconds:.2f}")
            await asyncio.sleep(CHUNK_MS / 1000.0)
        await ws.send_json({"type": "input_audio_buffer.commit"})
        try:
            await asyncio.wait_for(asyncio.sleep(6), timeout=7)
        except asyncio.TimeoutError:
            pass
        reader_task.cancel()
        await asyncio.gather(reader_task, return_exceptions=True)
        await ws.close()

    print(f"\n  == summary [{label}] ==")
    print(f"  event types: {json.dumps(event_types, ensure_ascii=False)}")
    print(f"  finals: {len(finals)}")
    joined = "".join(f.get("raw", {}).get("transcript", "") for f in finals)
    print(f"  total transcript chars: {len(joined)}")
    print(f"  transcript: {joined}")
    out = Path.cwd() / f"asr_events_{label}.json"
    out.write_text(json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  raw events -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--silence-ms", type=int, default=600)
    parser.add_argument("--commit-every", type=float, default=None)
    parser.add_argument("--label", default="run")
    args = parser.parse_args()
    asyncio.run(run(args.audio, args.silence_ms, args.commit_every, args.label))


main()
