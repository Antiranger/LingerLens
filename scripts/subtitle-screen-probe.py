"""Controlled media -> production server/player -> paid provider onset test."""
from __future__ import annotations
import argparse, asyncio, contextlib, dataclasses, hashlib, json, os, subprocess, sys, time, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
# Contains local transcript/media evidence; use only non-sensitive fixtures and never commit outputs.
ROOT=Path(os.environ.get('LINGERLENS_TEST_ROOT',str(ROOT)))
import urllib.request
if '--live-url' in sys.argv:
    system_proxies=urllib.request.getproxies()
    proxy=system_proxies.get('https') or system_proxies.get('http')
    if proxy:
        for key in ('HTTPS_PROXY','HTTP_PROXY','ALL_PROXY'): os.environ[key]=proxy
        os.environ['NO_PROXY']='127.0.0.1,localhost,::1'
sys.path.insert(0,str(ROOT/'prototype/hls-companion'))
from aiohttp import web
from playwright.async_api import async_playwright
from companion import server
from companion.providers.config import resolve_secrets
from companion.ytdlp_ingest import YtDlpLiveIngest
from companion.subtitle_pipeline import SubtitlePipeline

class FixtureIngest(YtDlpLiveIngest):
    fixture=None
    def command(self, selector=None):
        video=(selector=='video')
        return ['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-re',
            '-stream_loop','-1','-i',str(self.fixture),'-map','0:v:0' if video else '0:a:0','-c','copy',
            '-copyts','-output_ts_offset','10000','-muxdelay','0','-f','mpegts','pipe:1']

class ObservedPipeline(SubtitlePipeline):
    traces=None
    captured_pcm=bytearray()
    async def _enqueue_pcm_chunk(self,chunk,chunk_start):
        if len(self.captured_pcm)<10*1024*1024: self.captured_pcm.extend(chunk)
        await super()._enqueue_pcm_chunk(chunk,chunk_start)
    async def _handle_asr_event(self,event):
        self.traces.append({'at':time.monotonic(),'kind':'event','type':event.type,
            'item':event.item_id,'begin':event.begin_pcm,'end':event.end_pcm,
            'text':event.text,'translation':event.translation,'rawItem':(event.raw or {}).get('item_id')})
        await super()._handle_asr_event(event)
    def _materialize_cue(self,pending,epoch):
        cue=super()._materialize_cue(pending,epoch)
        if cue: self.traces.append({'at':time.monotonic(),'kind':'cue','epoch':epoch,
            'beginPCM':pending.begin_pcm,'endPCM':pending.end_pcm,
            'anchor':self.media_anchor.offset if self.media_anchor else None,
            'cue':cue.to_dict()})
        return cue

def info():
    return {'id':'fixture1234','title':'Controlled subtitle clock fixture','is_live':True,
        'live_status':'is_live','extractor_key':'Youtube','uploader':'local fixture',
        'webpage_url':'https://www.youtube.com/watch?v=fixture1234','formats':[
            {'format_id':'video','url':'https://example.invalid/video.m3u8','protocol':'m3u8_native',
             'vcodec':'avc1.64001e','acodec':'none','width':640,'height':360,'fps':25,'tbr':500,'ext':'mp4'},
            {'format_id':'audio','url':'https://example.invalid/audio.m3u8','protocol':'m3u8_native',
             'vcodec':'none','acodec':'mp4a.40.2','abr':96,'ext':'m4a'}]}

OBSERVER = "() => {\n const v=document.getElementById('video'), traces=[];\n const canvas=document.createElement('canvas');canvas.width=32;canvas.height=18;\n const ctx=canvas.getContext('2d',{willReadFrequently:true});\n let previous='',white=false,frames=0;\n function frame(now,meta) {\n   if(traces.length>=5000)return;\n   frames++;\n   // rVFC callbacks run before rAF. Read after production has painted this\n   // frame, once per VIDEO frame; never run an independent display-rate loop.\n   requestAnimationFrame(()=>{\n     const rows=[...document.querySelectorAll('#subtitleLayer .subtitle-cue-row')].map(r=>({\n       id:r.dataset.cueId,source:r.querySelector('.subtitle-src')?.textContent,\n       translation:r.querySelector('.subtitle-zh')?.textContent}));\n     const dn=document.getElementById('subtitleDraft'),draft=dn&&!dn.hidden?dn.textContent:'';\n     const text=JSON.stringify({rows,draft});let bright=false;\n     try{ctx.drawImage(v,0,0,32,18);bright=ctx.getImageData(2,2,1,1).data[0]>200;}catch{}\n     if(text!==previous||bright!==white)traces.push({media:meta.mediaTime,current:v.currentTime,\n       at:performance.now(),frame:frames,wall:window.acceptanceClock?.wallTimeForMediaPosition(meta.mediaTime),\n       rows,draft,flash:bright,flashOn:bright&&!white});\n     previous=text;white=bright;\n   });\n   v.requestVideoFrameCallback(frame);\n }\n v.requestVideoFrameCallback(frame);window.subtitleAcceptance=traces;\n}\n"

async def run(args):
    out=args.output;out.mkdir(parents=True,exist_ok=False)
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    config['asr']['active']=args.profile
    if args.native_fallback:
        next(p for p in config['asr']['providers'] if p['id']==args.profile).setdefault('options',{})['nativeTranslationFallback']=True
    if args.model_override:
        entry=next(p for p in config['asr']['providers'] if p['id']==args.profile)
        entry['model']=args.model_override
        entry.setdefault('options',{})['silenceDurationMs']=800
    config['subtitle'].update(sourceLanguage={'mode':'specified','tag':args.language},targetLanguage='zh-Hans')
    # The temporary file contains environment references, never API keys.
    for section in ("asr", "translation"):
        for index, entry in enumerate(config[section]["providers"]):
            key = entry.pop("apiKey", None)
            entry.pop("_apiKey", None)
            if key is not None:
                variable = "LINGERLENS_ACCEPTANCE_" + section.upper() + "_" + str(index)
                os.environ[variable] = key
                entry["apiKeyEnv"] = variable
    private_config=out/'providers.json'
    private_config.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
    FixtureIngest.fixture=args.fixture
    if not args.live_url: server.YtDlpLiveIngest=FixtureIngest
    server.SubtitlePipeline=ObservedPipeline
    events=[];ObservedPipeline.traces=events
    app=server.CompanionApplication(argparse.Namespace(runtime_dir=out/'media',providers_file=private_config,
        publish_delay=3.,cookies_from_browser=None,host='127.0.0.1',port=0),enable_native_control=False)
    if not args.live_url: app.probe.extract=lambda *a,**kw: info()
    application=app.routes();application.middlewares.append(server.errors)
    runner=web.AppRunner(application,access_log=None);await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
    port=site._server.sockets[0].getsockname()[1];app.args.port=port
    status=[]; result={'profile':args.profile}; browser=None
    try:
        async with async_playwright() as p:
            browser=await p.chromium.launch(channel='chrome',headless=True)
            page=await browser.new_page(viewport={'width':1680,'height':1050},locale='en-US')
            page_errors=[];page.on('pageerror',lambda e:page_errors.append(str(e)))
            await page.add_init_script("""
                Object.defineProperty(window,'createMediaClock',{configurable:true,
                  set(factory){this._clockFactory=opts=>{const c=factory(opts);window.acceptanceClock=c;
                    window.acceptanceHls=opts.hlsProvider||opts.getHls;return c;};},
                  get(){return this._clockFactory;}});
            """)
            async def start_route(route):
                body=route.request.post_data_json;body['liveMessages']={'enabled':False,'translate':False};body['proxyMode']='system'
                await route.continue_(post_data=json.dumps(body))
            await page.route('**/api/start',start_route)
            await page.route('**/api/probe',start_route)
            await page.goto(f'http://127.0.0.1:{port}/')
            await page.evaluate(OBSERVER)
            await page.fill('#url',args.live_url or 'https://www.youtube.com/watch?v=fixture1234')
            await page.click('#probe')
            await page.wait_for_function("!document.getElementById('probe').disabled",timeout=60000)
            if await page.locator('#start').is_disabled():
                result['probeFailure']=await page.locator('#setupFeedback').text_content()
                print(json.dumps({'probeFailure':result['probeFailure']},ensure_ascii=False),flush=True)
                raise RuntimeError("acceptance-probe-failed")
            if await page.locator('#start').is_disabled():
                result['probeFailure']=await page.locator('#setupFeedback').text_content()
                print(json.dumps({'probeFailure':result['probeFailure']},ensure_ascii=False),flush=True)
                raise RuntimeError("acceptance-probe-failed")
            await page.locator('#subtitlesEnabled').check()
            await page.fill('#targetDelay','15')
            await page.click('#start')
            start=time.monotonic()
            while time.monotonic()-start<args.seconds:
                await asyncio.sleep(.5)
                data=await page.evaluate("async()=>await fetch('/api/status').then(r=>r.json())")
                # Only explicitly selected non-secret timing fields are retained.
                sub=data.get('subtitles',{})
                status.append({'at':time.monotonic(),'state':data.get('state'),'epoch':data.get('pdtEpoch'),
                    'privateMedia':data.get('privateMediaSeconds'),'privateEdge':data.get('privateEdgeWallTime'),
                    'subtitles':{k:v for k,v in sub.items() if isinstance(v,(int,float,bool)) or v is None}})
                if data.get('state')=='error':
                    result['backendError']=str(data.get('error'))[:150];break
            result['browser']=await page.evaluate('window.subtitleAcceptance')
            result['browserErrors']=page_errors
            result['ui']=await page.evaluate("()=>({message:document.getElementById('message').textContent,current:document.getElementById('video').currentTime,paused:document.getElementById('video').paused})")
            result['cues']=[c.to_dict() for c in app.subtitle_store.query()] if app.subtitle_store else []
            pipeline=app.subtitle_pipeline
            result['anchor']=pipeline.media_anchor.offset if pipeline and pipeline.media_anchor else None
            result['epoch']=pipeline.media_epoch if pipeline else None
            result['sourceOrigins']=[getattr(x,'source_pts_first',None) for x in (app.source_ingest,app.asr_audio_ingest)]
            result['clock']=await page.evaluate("""()=>{const h=window.acceptanceHls?.(), v=document.getElementById('video');
                return {current:v.currentTime,wall:window.acceptanceClock?.playingWallTime(),
                 fragments:h?.levels?.[h.currentLevel]?.details?.fragments?.slice(0,3).map(f=>({start:f.start,startPTS:f.startPTS,pdt:f.programDateTime,duration:f.duration}))};}""")
            captured=out/'capture';captured.mkdir(exist_ok=True)
            for file in app.session.private_dir.iterdir():
                if file.suffix in ('.mp4','.m4s','.m3u8'): shutil.copyfile(file,captured/file.name)
            playlist=captured/'live.m3u8'
            if playlist.exists(): playlist.write_text(playlist.read_text(encoding='utf-8')+chr(10)+'#EXT-X-ENDLIST'+chr(10),encoding='utf-8')
            (out/'asr-decoded.pcm').write_bytes(ObservedPipeline.captured_pcm)
            await browser.close();browser=None
    finally:
        result['events']=events;result['status']=status
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        await app._stop_recovery_monitor()
        try: await app._teardown_session()
        except Exception as exc: result['cleanupError']=type(exc).__name__
        try: await runner.cleanup()
        except Exception as exc: result['runnerCleanupError']=type(exc).__name__
        if browser: await browser.close()
        private_config.unlink(missing_ok=True)
        result['events']=events;result['status']=status
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('events','status','cues','browser')},ensure_ascii=False),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',type=Path,required=True);ap.add_argument('--profile',required=True)
    ap.add_argument('--fixture',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--native-fallback',action='store_true');ap.add_argument('--model-override');ap.add_argument('--live-url');ap.add_argument('--language',default='en');ap.add_argument('--seconds',type=int,default=50)
    ap.add_argument('--confirm-paid',action='store_true');args=ap.parse_args()
    if not args.confirm_paid or not 1<=args.seconds<=120:ap.error('paid consent and bounded seconds required')
    sys.path.insert(0,str(ROOT/'desktop'))
    from windows_job import own_process_tree
    owned_tree=own_process_tree()
    asyncio.run(run(args))
