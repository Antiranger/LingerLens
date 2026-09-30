#!/usr/bin/env python3
"""Explicit, duration-capped live ASR acceptance probe. May incur provider charges.

No cloud request occurs without --confirm-paid. It never edits saved settings or
prints credentials/URLs. Input is an explicitly supplied mono PCM WAV at the negotiated sample rate.
This measures paced fixture-to-provider events, not end-to-end screen latency.
"""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
import dataclasses
import hashlib
import json
from pathlib import Path
import sys
import time
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'prototype/hls-companion'))
from companion.providers import create_asr
from companion.providers.base import SourceLanguagePolicy, validate_source_policy
from companion.providers.config import resolve_secrets
from companion.providers.readiness import validate_asr_start

def percentile(values, fraction):
    if not values: return None
    return sorted(values)[min(len(values)-1,int((len(values)-1)*fraction))]

async def probe(args):
    config=resolve_secrets(json.loads(args.config.read_text(encoding='utf-8')))
    profile=next((p for p in config.get('asr',{}).get('providers',[]) if p.get('id')==args.provider),None)
    if profile is None: raise ValueError('requested provider id is not configured')
    validate_asr_start(profile)
    provider=create_asr(profile)
    policy=SourceLanguagePolicy.specified(args.language)
    validate_source_policy(policy,provider.capabilities.language)
    rate=provider.capabilities.preferred_sample_rate or 16000
    with wave.open(str(args.audio),'rb') as audio:
        if audio.getnchannels()!=1 or audio.getsampwidth()!=2 or audio.getframerate()!=rate:
            raise ValueError(f'fixture must be mono PCM16 WAV at {rate} Hz')
        pcm=audio.readframes(int(rate*args.max_seconds))
    if not pcm: raise ValueError('empty audio fixture')
    if provider.capabilities.native_translation.enabled:
        provider.set_translation_target(args.target)
    report={'provider':{'id':provider.id,'kind':profile['kind'],'model':provider.model},
            'fixtureSha256':hashlib.sha256(pcm).hexdigest(),'audioSeconds':len(pcm)/(rate*2),
            'measurement':'paced fixture to ASR events; not screen latency',
            'cloudAttempted':False,'completed':False,'errorCategory':None}
    counts=Counter(); kinds=Counter(); lag=[]; first=None; first_translation=None; translated_finals=0; trusted_anchor_events=0; spans={}; events=[]
    stream=None; reader=None; start=None
    try:
        report['cloudAttempted']=True
        stream=await asyncio.wait_for(provider.stream(policy=policy,sample_rate=rate,hotwords=[],context=[]),15)
        start=time.monotonic()
        async def consume():
            nonlocal first, first_translation, translated_finals, trusted_anchor_events
            async for event in stream:
                at=time.monotonic()-start
                if event.type=='error': raise RuntimeError('provider reported an error')
                counts[event.type]+=1
                if first is None and event.type in ('interim','final'): first=at
                if first_translation is None and (event.translation or event.translation_stash): first_translation=at
                if event.type=='final' and event.translation: translated_finals+=1
                if event.translation_anchors_trusted and event.translation_anchors: trusted_anchor_events+=1
                if event.type=='speech_stopped' and event.item_id:
                    spans[event.item_id]=event.end_pcm
                    while len(spans)>128: spans.pop(next(iter(spans)))
                observation=event.caption_observation
                if observation is not None: kinds[observation.kind]+=1
                if event.type=='final':
                    end=event.end_pcm if event.end_pcm is not None else spans.pop(event.item_id,None)
                    if end is not None: lag.append(round((at-end)*1000,3))
                if args.events_output and len(events)<4096:
                    # Explicit opt-in artifact may contain the supplied fixture's transcript.
                    events.append({'receivedSeconds':at,'eventType':event.type,
                        'observation':dataclasses.asdict(observation) if observation else None})
        reader=asyncio.create_task(consume())
        chunk_bytes=int(rate*2*0.2)
        for offset in range(0,len(pcm),chunk_bytes):
            if reader.done():
                await reader
                raise RuntimeError('provider closed before all audio was sent')
            wait=start+offset/(rate*2)-time.monotonic()
            if wait>0: await asyncio.sleep(wait)
            await asyncio.wait_for(stream.push_pcm(pcm[offset:offset+chunk_bytes],offset/(rate*2)),5)
        await asyncio.wait_for(stream.flush(),5)
        await asyncio.sleep(2)
        await asyncio.wait_for(stream.aclose(),15)
        try: await asyncio.wait_for(reader,3)
        except asyncio.TimeoutError: pass
        report['completed']=True
    except Exception as error:
        # Provider exceptions may include signed URLs; report only the category.
        report['errorCategory']=type(error).__name__
    finally:
        if stream is not None:
            try: await asyncio.wait_for(stream.aclose(),3)
            except Exception: pass
            session=getattr(stream,'session',None)
            if session is not None and not session.closed: await session.close()
        if reader is not None:
            reader.cancel(); await asyncio.gather(reader,return_exceptions=True)
    report.update(eventCounts=dict(counts),observationKinds=dict(kinds),firstTranscriptSeconds=first,
        firstTranslationSeconds=first_translation,translatedFinals=translated_finals,
        trustedTranslationAnchorEvents=trusted_anchor_events,
        finalLagMs={'samples':len(lag),'p50':percentile(lag,.5),'p95':percentile(lag,.95)},
        wallSeconds=round(time.monotonic()-start,3) if start is not None else None)
    if args.events_output:
        with args.events_output.open('x',encoding='utf-8') as output:
            for event in events: output.write(json.dumps(event,ensure_ascii=False)+chr(10))
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--provider',required=True)
    parser.add_argument('--audio',type=Path,required=True)
    parser.add_argument('--language',default='en')
    parser.add_argument('--target',default='zh-Hans')
    parser.add_argument('--max-seconds',type=int,default=30)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--events-output',type=Path)
    parser.add_argument('--confirm-paid',action='store_true')
    args=parser.parse_args()
    if not args.confirm_paid: parser.error('--confirm-paid is required; cloud requests may be billed')
    if not 1<=args.max_seconds<=120: parser.error('--max-seconds must be 1..120')
    if args.output.exists() or (args.events_output and args.events_output.exists()): parser.error('refusing to overwrite evidence')
    report=asyncio.run(probe(args))
    with args.output.open('x',encoding='utf-8') as output: json.dump(report,output,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report['completed'] and report['eventCounts'].get('final') else 1

if __name__=='__main__': raise SystemExit(main())
