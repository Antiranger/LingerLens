"""Paced, paid provider-to-cue measurement; no network before --confirm-paid.
Private output may contain the supplied fixture's transcript. Never commit it.
This isolates recognition/readiness from acquisition and screen-clock mapping.
"""
from __future__ import annotations
import argparse, asyncio, dataclasses, hashlib, json, sys, time, wave
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'prototype/hls-companion'))
from companion.providers import create_asr, create_translation
from companion.providers.config import resolve_secrets
from companion.providers.base import SourceLanguagePolicy, StreamMeta
from companion.providers.native_session import NativeTranslationBus, NativeSessionTranslation
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore

def pct(xs, q):
    return sorted(xs)[round((len(xs)-1)*q)] if xs else None

async def run_case(config, profile_id, args):
    record = next(p for p in config['asr']['providers'] if p['id'] == profile_id)
    if args.silence_ms and record['kind']=='dashscope-livetranslate-realtime':
        record={**record,'options':{**record.get('options',{}),'silenceDurationMs':args.silence_ms}}
    if args.model_override: record={**record,'model':args.model_override}
    provider = create_asr(record)
    rate = provider.capabilities.preferred_sample_rate or 16000
    with wave.open(str(args.audio), 'rb') as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1,2,rate):
            raise ValueError('fixture must match negotiated mono PCM16 rate')
        pcm = w.readframes(int(args.seconds * rate))
    bus = NativeTranslationBus() if provider.capabilities.native_translation.enabled else None
    if bus:
        fallback = create_translation(next(p for p in config['translation']['providers'] if p['id']==config['translation']['active'])) if args.native_fallback else None
        provider.set_translation_target(args.target)
        mt = NativeSessionTranslation(bus, provider_id=profile_id+':native', label='native', model=provider.model, fallback=fallback)
    else:
        mt = create_translation(next(p for p in config['translation']['providers'] if p['id']==config['translation']['active']))
    if args.native_turn_mode and bus is not None:
        from companion.providers.asr_dashscope_livetranslate import _LiveTranslateStream
        original_update=_LiveTranslateStream._session_update
        def update(stream):
            payload=original_update(stream); session=payload['session']
            if args.native_turn_mode=='default': session.pop('turn_detection',None)
            elif args.native_turn_mode=='speaker':
                session.pop('turn_detection',None)
                session['audio']={'input':{'turn_detection':{'type':'speaker_detection','threshold':0.5,'silence_duration_ms':800}}}
            elif args.native_turn_mode=='explicit-pcm':
                session['input_audio_format']='pcm'; session['sample_rate']=16000
            elif args.native_turn_mode=='source-asr':
                session['input_audio_format']='pcm'; session['sample_rate']=16000
                session['input_audio_transcription']={'model':'qwen3-asr-flash-realtime','language':args.language}
            elif args.native_turn_mode=='no-interrupt': session['turn_detection']['interrupt_response']=False
            return payload
        _LiveTranslateStream._session_update=update
    policy = SourceLanguagePolicy.specified(args.language)
    pipeline = SubtitlePipeline(asr_provider=provider, translation_provider=mt,
        cue_store=CueStore(), meta=StreamMeta('fixture','local','',args.language,args.target),
        source_policy=policy, sample_rate=rate, native_translation_bus=bus,
        playback_delay_seconds=lambda: 15.0, translation_timeout_seconds=(float(record.get('options',{}).get('nativeTranslationTimeoutSeconds',15)) if bus else float(next(p for p in config['translation']['providers'] if p['id']==config['translation']['active']).get('options',{}).get('timeoutSeconds',6))))
    trace, updates, first_seen, revisions = [], [], {}, {}
    raw_events = []
    stream = None; tasks = []; error = None; started = time.monotonic()
    try:
        stream = await asyncio.wait_for(provider.stream(policy=policy,sample_rate=rate,hotwords=[],context=[]),20)
        pipeline._running = True; pipeline._stream = stream
        pipeline._pcm_queue = asyncio.Queue(maxsize=32)
        started = time.monotonic(); pipeline.media_epoch = time.time()
        mapper=stream._map_event
        def traced_map(raw):
            raw_events.append({'at':time.monotonic()-started, **{k:v for k,v in raw.items() if k in
                ('type','item_id','response_id','delta','transcript','text','audio_start_ms','audio_end_ms','response','session','item','previous_item_id')}})
            return mapper(raw)
        stream._map_event=traced_map
        async def consume():
            async for ev in stream:
                at = time.monotonic()-started
                raw = ev.raw or {}
                trace.append({'at':at,'type':ev.type,'item':ev.item_id,'begin':ev.begin_pcm,'end':ev.end_pcm,
                    'text':ev.text,'translation':ev.translation,'rawType':raw.get('type'),
                    'responseId':raw.get('response_id') or (raw.get('response') or {}).get('id'),
                    'errorCode':(raw.get('error') or {}).get('code') if isinstance(raw.get('error'),dict) else None, 'errorMessage':str(ev.message or '')[:240] if ev.type=='error' else None, 'observation':dataclasses.asdict(ev.caption_observation) if ev.caption_observation else None})
                if ev.type=='error': raise RuntimeError('provider-error: '+str(ev.message)[:120])
                await pipeline._handle_asr_event(ev)
        async def sample():
            while True:
                at = time.monotonic()-started
                for cue in pipeline.store.query():
                    if revisions.get(cue.id) == cue.seq: continue
                    revisions[cue.id] = cue.seq
                    start = cue.t_start-pipeline.media_epoch if cue.t_start is not None else None
                    first_seen.setdefault(cue.id,at)
                    updates.append({'at':at,'id':cue.id,'seq':cue.seq,'state':cue.state,
                        'begin':start,'end':cue.t_end-pipeline.media_epoch,'text':cue.src,'translation':cue.zh,
                        'reason':cue.cut_reason,'lateAt15':None if start is None else at-start-15})
                await asyncio.sleep(.02)
        tasks = [asyncio.create_task(consume()),asyncio.create_task(pipeline._pcm_sender()),
            asyncio.create_task(pipeline._caption_deadline_worker()),asyncio.create_task(sample())]
        tasks += [asyncio.create_task(pipeline._translation_worker()) for _ in range(4)]
        chunk_bytes = rate*2//5
        for offset in range(0,len(pcm),chunk_bytes):
            data=pcm[offset:offset+chunk_bytes]
            until=started+(offset+len(data))/(rate*2)-time.monotonic()
            if until>0: await asyncio.sleep(until)
            if tasks[0].done(): await tasks[0]; raise RuntimeError('stream ended early')
            await pipeline._enqueue_pcm_chunk(data,offset/(rate*2))
        # Provider-native finish/drain; do not force segmentation during speech.
        await asyncio.sleep(1)
        await asyncio.wait_for(stream.aclose(),20)
        pipeline._flush_caption_session()
        await asyncio.sleep(3)
    except Exception as exc:
        error=type(exc).__name__
    finally:
        pipeline._running=False
        if stream:
            try: await asyncio.wait_for(stream.aclose(),3)
            except Exception: pass
            session=getattr(stream,'session',None)
            if session is not None and not session.closed: await session.close()
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    source=[u for u in updates if first_seen[u['id']]==u['at']]
    done=[u for u in updates if u['state']=='done']
    def summary(rows):
        lag=[u['at']-u['begin'] for u in rows if u['begin'] is not None]
        return {'count':len(rows),'readyFromOnsetP50':pct(lag,.5),'readyFromOnsetP95':pct(lag,.95),
            'lateAt15':sum(v>15 for v in lag),'maxReadyFromOnset':max(lag) if lag else None}
    if not source and error is None: error='NoSourceEvidence'
    result={'profile':profile_id,'model':provider.model,'native':bus is not None,'fallbackEnabled':bool(bus is not None and args.native_fallback),
        'audioSeconds':pipeline._stream_pushed_seconds,'fixtureAudioSeconds':len(pcm)/(rate*2),'completedInput':error is None and pipeline._stream_pushed_seconds >= len(pcm)/(rate*2)-.001,'audioSha256':hashlib.sha256(pcm).hexdigest(),
        'errorCategory':error,'source':summary(source),'translation':summary(done),
        'events':trace,'updates':updates,'rawEvents':raw_events if stream else []}
    (args.output/(profile_id+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('events','updates','rawEvents')},ensure_ascii=False),flush=True)
    return error is None

async def main_async(args):
    config=resolve_secrets(json.loads(args.config.read_text(encoding='utf-8-sig')))
    outcomes=[]
    for pair in (args.profiles[:2],args.profiles[2:]):
        outcomes.extend(await asyncio.gather(*(run_case(config,p,args) for p in pair)))
    return all(outcomes)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--profiles',nargs='+',required=True)
    parser.add_argument('--audio',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=40)
    parser.add_argument('--model-override')
    parser.add_argument('--native-fallback',action='store_true',help='explicitly allow extra MT charges for missing/unaligned native translations')
    parser.add_argument('--silence-ms',type=int)
    parser.add_argument('--native-turn-mode',choices=['default','speaker','no-interrupt','explicit-pcm','source-asr'])
    parser.add_argument('--language',default='ja')
    parser.add_argument('--target',default='zh-Hans')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--confirm-paid',action='store_true')
    args=parser.parse_args()
    if not args.confirm_paid or not 1<=args.seconds<=120 or not 1<=len(args.profiles)<=4:
        parser.error('requires --confirm-paid, seconds 1..120 and 1..4 profiles')
    args.output.mkdir(parents=True,exist_ok=False)
    if any(not p or p in (".","..") or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in p) for p in args.profiles):
        parser.error("profile ids must be safe file-name components")
    raise SystemExit(0 if asyncio.run(main_async(args)) else 1)
