"""Adapter -> actual SubtitlePipeline -> actual CueStore contract replay.

No sockets or paid API calls. This proves local integration, not account access
or live latency. The same three utterances include a genuine repeated phrase.
"""
import copy
import unittest
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

TEXTS=('This is the first complete sentence.', 'The second sentence is different.', 'This is the first complete sentence.')

def setup(kind):
    cfg=copy.deepcopy(next(p for p in BUILTIN_ASR_PROVIDERS if p['kind']==kind and p['model']!='gpt-live-transcribe'))
    cfg.setdefault('options',{})['translationType']=''
    provider=create_asr(cfg); policy=SourceLanguagePolicy.specified('en')
    rate=provider.capabilities.preferred_sample_rate or 16000
    constructors={
        'soniox-realtime':lambda:_SonioxStream(provider,policy,rate,[]),
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
    if kind=='soniox-realtime':
        return stream._map_event({'tokens':[{'text':text,'start_ms':start*1000,'end_ms':end*1000,'is_final':True,'language':'en'},{'text':'<end>','is_final':True}]})
    if kind in ('dashscope-qwen-realtime','openai-realtime-transcription'):
        return [stream._map_event(raw) for raw in [
            {'type':'input_audio_buffer.speech_started','item_id':item,'audio_start_ms':start*1000},
            {'type':'input_audio_buffer.speech_stopped','item_id':item,'audio_end_ms':end*1000},
            {'type':'conversation.item.input_audio_transcription.completed','item_id':item,'transcript':text,'language':'en'}]]
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

KINDS=('soniox-realtime','dashscope-qwen-realtime','dashscope-task-asr','deepgram-streaming','assemblyai-streaming','elevenlabs-scribe-realtime','speechmatics-realtime','tencent-asr','volcengine-sauc','openai-realtime-transcription')

class ProviderPipelineMatrix(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,kind):
        provider,stream,rate=setup(kind)
        pipeline=SubtitlePipeline(asr_provider=provider,cue_store=CueStore(),meta=StreamMeta('fixture','test',None,'en','zh-Hans'),sample_rate=rate)
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

def case(kind):
    async def test(self): await self.exercise(kind)
    return test
for kind in KINDS: setattr(ProviderPipelineMatrix,'test_'+kind.replace('-','_'),case(kind))

if __name__=='__main__': unittest.main()
