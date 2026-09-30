"""Adapter -> actual SubtitlePipeline -> actual CueStore contract replay.

No sockets or paid API calls. This proves local integration, not account access
or live latency. The same three utterances include a genuine repeated phrase.
"""
import copy
import asyncio
import unittest
from aiohttp import web
from companion.providers import create_asr
from companion.providers.config import BUILTIN_ASR_PROVIDERS
from companion.providers.base import SourceLanguagePolicy, StreamMeta
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore
from companion.providers.asr_soniox_realtime import _SonioxStream
from companion.providers.asr_qwen_realtime import _QwenRealtimeStream
from companion.providers.asr_dashscope_task import _DashScopeTaskStream
from companion.providers.asr_deepgram_streaming import _DeepgramStream
from companion.providers.asr_assemblyai_streaming import _AssemblyAIStream
from companion.providers.asr_elevenlabs_scribe_realtime import _ElevenLabsScribeStream
from companion.providers.asr_speechmatics_realtime import _SpeechmaticsStream
from companion.providers.asr_tencent_asr import _TencentAsrStream
from companion.providers.asr_volcengine_sauc import _VolcengineSaucStream
from companion.providers.asr_openai_realtime_transcription import _OpenAIRealtimeStream
from companion.providers.asr_dashscope_livetranslate import _LiveTranslateStream
from companion.providers.native_session import NativeTranslationBus
from companion.caption_chunker import HARD_DEADLINE_SECONDS
from test_subtitle_pipeline import RecordingTranslation

TEXTS=('This is the first complete sentence.', 'The second sentence is different.', 'This is the first complete sentence.')

def setup(kind):
    cfg=copy.deepcopy(next(p for p in BUILTIN_ASR_PROVIDERS if p['kind']==kind and p['model']!='gpt-live-transcribe'))
    cfg.setdefault('options',{})['translationType']=''
    provider=create_asr(cfg); policy=SourceLanguagePolicy.specified('en')
    rate=provider.capabilities.preferred_sample_rate or 16000
    constructors={
        'soniox-realtime':lambda:_SonioxStream(provider,policy,rate,[]),
        'soniox-realtime-transcribe':lambda:_SonioxStream(provider,policy,rate,[]),
        'dashscope-livetranslate-realtime':lambda:_LiveTranslateStream(provider,policy,rate),
        'dashscope-qwen-realtime':lambda:_QwenRealtimeStream(provider,'en',rate),
        'dashscope-task-asr':lambda:_DashScopeTaskStream(provider,['en'],rate,[]),
        'deepgram-streaming':lambda:_DeepgramStream(provider,policy,rate),
        'assemblyai-streaming':lambda:_AssemblyAIStream(provider,policy,rate),
        'elevenlabs-scribe-realtime':lambda:_ElevenLabsScribeStream(provider,policy,rate),
        'speechmatics-realtime':lambda:_SpeechmaticsStream(provider,policy,rate),
        'tencent-asr':lambda:_TencentAsrStream(provider,policy,rate),
        'volcengine-sauc':lambda:_VolcengineSaucStream(provider,policy,rate),
        'openai-realtime-transcription':lambda:_OpenAIRealtimeStream(provider,policy,rate),
    }
    return provider,constructors[kind](),rate

def events(kind,stream,index,text):
    start,end=index*5.,index*5.+3.
    item=str(index)
    if kind in ('soniox-realtime', 'soniox-realtime-transcribe'):
        return stream._map_event({'tokens':[{'text':text,'start_ms':start*1000,'end_ms':end*1000,'is_final':True,'language':'en'},{'text':'<end>','is_final':True}]})
    if kind in ('dashscope-qwen-realtime','openai-realtime-transcription','dashscope-livetranslate-realtime'):
        mapped = [stream._map_event(raw) for raw in [
            {'type':'input_audio_buffer.speech_started','item_id':item,'audio_start_ms':start*1000},
            {'type':'input_audio_buffer.speech_stopped','item_id':item,'audio_end_ms':end*1000},
            {'type':'conversation.item.input_audio_transcription.completed','item_id':item,'transcript':text,'language':'en'}]]
        return [event for batch in mapped for event in batch] if kind == 'dashscope-livetranslate-realtime' else mapped
    if kind=='dashscope-task-asr':
        return [stream._map_event({'header':{'event':'result-generated'},'payload':{'output':{'sentence':{'sentence_id':index,'sentence_end':True,'text':text,'begin_time':start*1000,'end_time':end*1000}}}})]
    if kind=='deepgram-streaming':
        words=[{'word':text,'punctuated_word':text,'start':start,'end':end}]
        return stream._map_event({'type':'SpeechStarted','timestamp':start})+stream._map_event({'type':'Results','is_final':True,'speech_final':True,'channel':{'alternatives':[{'transcript':text,'words':words}]}})
    if kind=='assemblyai-streaming':
        return stream._map_event({'type':'Turn','turn_order':index,'end_of_turn':True,'turn_is_formatted':True,'transcript':text,'words':[{'text':text,'start':start*1000,'end':end*1000,'word_is_final':True}]})
    if kind=='elevenlabs-scribe-realtime':
        return [stream._map_event({'message_type':'committed_transcript','text':text,'language_code':'en'})]
    if kind=='speechmatics-realtime':
        return stream._map_event({'message':'AddTranscript','metadata':{'start_time':start,'end_time':end,'transcript':text}})
    if kind=='tencent-asr':
        return stream._map_event({'code':0,'result':{'slice_type':2,'index':index,'start_time':start*1000,'end_time':end*1000,'voice_text_str':text}})
    return stream._map_result({'result':{'utterances':[{'text':text,'start_time':start*1000,'end_time':end*1000,'definite':True}]}},is_last=False)

KINDS=('soniox-realtime','soniox-realtime-transcribe','dashscope-livetranslate-realtime','dashscope-qwen-realtime','dashscope-task-asr','deepgram-streaming','assemblyai-streaming','elevenlabs-scribe-realtime','speechmatics-realtime','tencent-asr','volcengine-sauc','openai-realtime-transcription')

class ProviderPipelineMatrix(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,kind):
        provider,stream,rate=setup(kind)
        translation = RecordingTranslation('fixture-mt')
        pipeline=SubtitlePipeline(asr_provider=provider,cue_store=CueStore(),meta=StreamMeta('fixture','test',None,'en','zh-Hans'),sample_rate=rate,
                                  translation_provider=translation if kind != 'dashscope-livetranslate-realtime' else None,
                                  native_translation_bus=NativeTranslationBus() if kind == 'dashscope-livetranslate-realtime' else None)
        pipeline.media_epoch=1000.
        pipeline._push_breadcrumbs.append((0.,0.))
        for index,text in enumerate(TEXTS):
            pipeline._pcm_offset=pipeline._last_sent_pcm_offset=index*5.+3.
            for event in events(kind,stream,index,text):
                if event is not None: await pipeline._handle_asr_event(event)
        pipeline._flush_caption_session()
        cues=pipeline.store.query()
        self.assertEqual([cue.src for cue in cues],list(TEXTS),kind)
        self.assertEqual(pipeline.stats.unmapped_observations,0,kind)
        self.assertEqual(len({cue.id for cue in cues}),3)
        self.assertTrue(all(cue.t_end>=cue.t_start for cue in cues))
        if kind == 'elevenlabs-scribe-realtime':
            self.assertEqual([(cue.t_start, cue.t_end) for cue in cues],
                             [(1000., 1003.), (1003., 1008.), (1008., 1013.)],
                             'untimed commits must not restart at the stream origin')
            self.assertTrue(all(cue.timing_source == 'approx' for cue in cues))
        else:
            self.assertEqual([(cue.t_start, cue.t_end) for cue in cues],
                             [(1000. + i * 5, 1003. + i * 5) for i in range(3)], kind)
        if kind != 'dashscope-livetranslate-realtime':
            pipeline._running = True
            worker = asyncio.create_task(pipeline._translation_worker())
            try:
                await asyncio.wait_for(pipeline._translation_queue.join(), 2)
                self.assertEqual([request.source_text for request in translation.requests], list(TEXTS), kind)
                self.assertEqual([cue.zh for cue in cues], ['译' + text for text in TEXTS], kind)
                self.assertTrue(all(cue.state == 'done' for cue in cues))
            finally:
                pipeline._running = False
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    async def test_mutable_text_is_not_force_published_at_the_deadline(self):
        text = 'a tentative hypothesis that can still change'
        for kind in ('dashscope-task-asr','openai-realtime-transcription','elevenlabs-scribe-realtime',
                     'tencent-asr','volcengine-sauc','speechmatics-realtime','assemblyai-streaming','deepgram-streaming'):
            with self.subTest(kind=kind):
                provider, stream, rate = setup(kind)
                pipeline = SubtitlePipeline(asr_provider=provider, cue_store=CueStore(),
                    meta=StreamMeta('fixture','test',None,'en','zh-Hans'),sample_rate=rate,monotonic=lambda:0.)
                pipeline.media_epoch = 1000.
                pipeline._push_breadcrumbs.append((0.,0.))
                pipeline._last_sent_pcm_offset = 6.
                if kind == 'dashscope-task-asr':
                    raw = {'sentence':{'text':text,'sentence_id':0,'sentence_end':False,'begin_time':0,'end_time':6000}}
                elif kind == 'openai-realtime-transcription':
                    raw = {'type':'conversation.item.input_audio_transcription.delta','item_id':'0','delta':text}
                elif kind == 'elevenlabs-scribe-realtime':
                    raw = {'message_type':'partial_transcript','text':text}
                elif kind == 'tencent-asr':
                    raw = {'code':0,'result':{'slice_type':1,'index':0,'voice_text_str':text,'start_time':0,'end_time':6000}}
                elif kind == 'volcengine-sauc':
                    raw = {'result':{'utterances':[{'text':text,'start_time':0,'end_time':6000,'definite':False}]}}
                elif kind == 'speechmatics-realtime':
                    raw = {'message':'AddPartialTranscript','metadata':{'transcript':text,'start_time':0,'end_time':6}}
                elif kind == 'assemblyai-streaming':
                    raw = {'type':'Turn','turn_order':0,'end_of_turn':False,'transcript':text,
                           'words':[{'text':text,'start':0,'end':6000,'word_is_final':False}]}
                else:
                    raw = {'type':'Results','is_final':False,'channel':{'alternatives':[{'transcript':text,
                           'words':[{'word':text,'start':0,'end':6}]}]}}
                batch = stream._map_result(raw,is_last=False) if kind == 'volcengine-sauc' else stream._map_event(raw)
                if not isinstance(batch,list): batch = [batch]
                for event in batch:
                    if event is not None: await pipeline._handle_asr_event(event)
                pipeline._materialize_caption_decision(pipeline.caption_chunker.expire(8.))
                self.assertEqual(pipeline.store.query(), [], kind)

    async def test_stable_evidence_releases_at_seven_seconds_without_endpoint(self):
        text = 'one two three four five six'
        for kind in ('soniox-realtime', 'soniox-realtime-transcribe', 'dashscope-qwen-realtime',
                     'dashscope-livetranslate-realtime', 'deepgram-streaming', 'assemblyai-streaming'):
            with self.subTest(kind=kind):
                provider, stream, rate = setup(kind)
                clock = [0.5]
                pipeline = SubtitlePipeline(asr_provider=provider, cue_store=CueStore(),
                    meta=StreamMeta('fixture','test',None,'en','zh-Hans'), sample_rate=rate,
                    monotonic=lambda: clock[0],
                    native_translation_bus=NativeTranslationBus() if kind == 'dashscope-livetranslate-realtime' else None)
                pipeline.media_epoch = 1000.
                pipeline._push_breadcrumbs.append((0., 20.))
                pipeline._pcm_offset = pipeline._last_sent_pcm_offset = 26.
                if kind.startswith('soniox'):
                    # Soniox tokenizer pieces close a whitespace word only when
                    # the next word begins; its last piece is intentionally held.
                    batch = stream._map_event({'tokens':[
                        {'text':(' ' if i else '') + word,'start_ms':i*1000,'end_ms':(i+1)*1000,'is_final':True,'language':'en'}
                        for i, word in enumerate((text + ' seven').split())]})
                elif kind in ('dashscope-qwen-realtime', 'dashscope-livetranslate-realtime'):
                    start = stream._map_event({'type':'input_audio_buffer.speech_started','item_id':'long','audio_start_ms':0})
                    batch = stream._map_event({'type':'conversation.item.input_audio_transcription.text','item_id':'long','text':text})
                    batch = (start + batch) if isinstance(batch, list) else [start, batch]
                elif kind == 'deepgram-streaming':
                    batch = stream._map_event({'type':'Results','is_final':True,'speech_final':False,
                        'channel':{'alternatives':[{'transcript':text,'words':[{'word':text,'start':0,'end':6}]}]}})
                else:
                    batch = stream._map_event({'type':'Turn','turn_order':0,'end_of_turn':False,
                        'transcript':text,'words':[{'text':text,'start':0,'end':6000,'word_is_final':True}]})
                for event in batch:
                    if event is not None: await pipeline._handle_asr_event(event)
                self.assertEqual(pipeline.store.query(), [])
                self.assertEqual(pipeline.caption_chunker.expire(clock[0] + HARD_DEADLINE_SECONDS - .001).chunks, ())
                clock[0] += HARD_DEADLINE_SECONDS
                pipeline._materialize_caption_decision(pipeline.caption_chunker.expire(clock[0]))
                cues = pipeline.store.query()
                self.assertEqual([cue.src for cue in cues], [text], kind)
                self.assertEqual((cues[0].t_start, cues[0].t_end), (1020., 1026.), kind)
                self.assertEqual(cues[0].cut_reason, 'hard_deadline')

    async def test_untimed_item_and_reused_ids_stay_inside_new_session(self):
        provider, stream, rate = setup('elevenlabs-scribe-realtime')
        pipeline = SubtitlePipeline(asr_provider=provider, cue_store=CueStore(),
            meta=StreamMeta('fixture','test',None,'en','zh-Hans'), sample_rate=rate)
        pipeline.media_epoch = 1000.
        pipeline._push_breadcrumbs.append((0., 40.))
        pipeline._pcm_offset = pipeline._last_sent_pcm_offset = 43.
        first = stream._map_event({'message_type':'committed_transcript','commit_id':'reused','text':'hello'})
        await pipeline._handle_asr_event(first)
        await pipeline._handle_asr_event(first)
        self.assertEqual(len(pipeline.store.query()), 1)
        pipeline._vad_spans.clear()
        pipeline._push_breadcrumbs.clear()
        pipeline._last_sent_pcm_offset = 80.
        pipeline._begin_generation()
        pipeline._push_breadcrumbs.append((0., 80.))
        pipeline._pcm_offset = pipeline._last_sent_pcm_offset = 83.
        # New item with the same provider ID must not inherit the old origin.
        await pipeline._handle_asr_event(first)
        self.assertEqual([(c.t_start,c.t_end) for c in pipeline.store.query()],
                         [(1040.,1043.),(1080.,1083.)])

    async def test_http_windows_pass_actual_adapter_into_pipeline(self):
        async def transcribe(request):
            await request.read()
            return web.json_response({'text':'hello again','language':'en'})
        app = web.Application()
        app.router.add_post('/audio/transcriptions', transcribe)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        stream = None
        try:
            port = site._server.sockets[0].getsockname()[1]
            provider = create_asr({'id':'http-fixture','kind':'openai-audio-transcriptions','model':'whisper-1',
                'baseUrl':f'http://127.0.0.1:{port}','options':{'windowSeconds':3,'maxPendingWindows':4}})
            pipeline = SubtitlePipeline(asr_provider=provider, cue_store=CueStore(),
                meta=StreamMeta('fixture','test',None,'en','zh-Hans'), sample_rate=16000)
            pipeline.media_epoch = 1000.
            pipeline._push_breadcrumbs.append((0., 0.))
            stream = await provider.stream(policy=SourceLanguagePolicy.specified('en'),sample_rate=16000,hotwords=[],context=[])
            for offset in (40.,43.,46.):
                await stream.push_pcm(b'\0' * 96000, offset)
            await stream.aclose()
            async for event in stream:
                await pipeline._handle_asr_event(event)
            pipeline._flush_caption_session()
            cues = pipeline.store.query()
            self.assertEqual([cue.src for cue in cues], ['hello again'] * 3)
            self.assertEqual([(c.t_start,c.t_end) for c in cues], [(1040.,1043.),(1043.,1046.),(1046.,1049.)])
        finally:
            if stream is not None: await stream.aclose()
            await runner.cleanup()

def case(kind):
    async def test(self): await self.exercise(kind)
    return test
for kind in KINDS: setattr(ProviderPipelineMatrix,'test_'+kind.replace('-','_'),case(kind))

if __name__=='__main__': unittest.main()
