import asyncio
import time
import unittest
from test_subtitle_pipeline import FakeASR, RecordingTranslation
from companion.providers.base import StreamMeta, TranslationResult, CaptionObservation, RecognitionToken
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore
from companion.caption_chunker import CaptionChunker

class Attempts(RecordingTranslation):
    def __init__(self,outcomes):
        super().__init__('attempts');self.outcomes=list(outcomes);self.calls=0
    async def translate(self,request):
        self.calls+=1
        outcome=self.outcomes[min(self.calls-1,len(self.outcomes)-1)]
        if isinstance(outcome,Exception):raise outcome
        if outcome=='SLOW':await asyncio.sleep(1)
        return TranslationResult(outcome,self.id,1)

class SingleAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self,outcomes,budget=1):
        provider=Attempts(outcomes)
        pipeline=SubtitlePipeline(asr_provider=FakeASR(),translation_provider=provider,
            cue_store=CueStore(),meta=StreamMeta('t','c','gaming','en','zh'),
            translation_timeout_seconds=budget,monotonic=time.monotonic)
        cue=pipeline.store.add(t_start=0,t_end=1,hold=1,src='source',lang='en',timing_source='asr')
        pipeline._enqueue_translation(cue);pipeline._running=True
        worker=asyncio.create_task(pipeline._translation_worker())
        try:await asyncio.wait_for(pipeline._translation_queue.join(),2)
        finally:
            pipeline._running=False;worker.cancel();await asyncio.gather(worker,return_exceptions=True)
        return cue,provider,pipeline
    async def test_empty_response_is_terminal_without_pipeline_retries(self):
        cue,p,pipeline=await self.run_case(['  ',RuntimeError('transient'),'译文'])
        self.assertEqual((cue.state,cue.zh,p.calls),('failed',None,1))
        self.assertEqual(pipeline.stats.translation_attempts,1)
        self.assertEqual(pipeline.stats.translation_provider_failures, 1)
    async def test_provider_failure_is_terminal_without_pipeline_retries(self):
        cue,p,_=await self.run_case([RuntimeError('offline')])
        self.assertEqual((cue.state,cue.zh,p.calls),('failed',None,1))
    async def test_empty_response_counts_as_one_provider_failure(self):
        cue,p,_=await self.run_case([''])
        self.assertEqual((cue.state,cue.zh,p.calls),('failed',None,1))
    async def test_timeout_shares_total_budget(self):
        begin=time.monotonic();cue,p,_=await self.run_case(['SLOW'],.05)
        self.assertLess(time.monotonic()-begin,.5)
        self.assertEqual((cue.state,cue.zh,p.calls),('failed',None,1))

class EvidenceTests(unittest.TestCase):
    def test_endpoint_does_not_discard_later_final(self):
        c=CaptionChunker(realtime=True)
        c.observe(CaptionObservation('stable_token_delta',1,'a',tokens=(RecognitionToken('first',0,1,True,language='en'),)))
        a=c.observe(CaptionObservation('endpoint',1,'a',end_pcm=1)).chunks
        b=c.observe(CaptionObservation('utterance_final',1,'a',tokens=(RecognitionToken('first',0,1,True,language='en'),RecognitionToken('second',1,2,True,language='en')),stable_text='first second')).chunks
        # 'first' is one second, below the 1.2s a provider endpoint may publish
        # at, so the endpoint releases nothing and the later final carries both
        # words out in one caption. Either way the later evidence is not
        # discarded, which is what this pins.
        self.assertFalse(a)
        self.assertEqual([x.text for x in a+b],['first second'])
        self.assertIsNone(c.next_deadline)
    def test_mutable_snapshots_never_claim_stability(self):
        c=CaptionChunker(realtime=True)
        for _ in range(4):
            self.assertFalse(c.observe(CaptionObservation('text_snapshot',1,'a',tentative_text='Wrong sentence.',begin_pcm=0,end_pcm=1)).chunks)
        # 1.4s so the final is past the provider-endpoint floor and this stays a
        # test about tentative text never claiming stability.
        result=c.observe(CaptionObservation('utterance_final',1,'a',stable_text='Correct sentence.',begin_pcm=0,end_pcm=1.4)).chunks
        self.assertEqual([x.text for x in result],['Correct sentence.'])
    def test_final_word_snapshot_deduplicates(self):
        c=CaptionChunker(realtime=True)
        # Timings past the 3.0s cut floor (punctuation_boundaries), so this stays a
        # test about deduplication rather than about the floor.
        tokens=(RecognitionToken('Hello.',0,3.2,True,language='en'),RecognitionToken('draft',3.2,4.0,False,language='en'))
        first=c.observe(CaptionObservation('token_snapshot',1,'a',tokens=tokens)).chunks
        second=c.observe(CaptionObservation('token_snapshot',1,'a',tokens=tokens)).chunks
        self.assertEqual([x.text for x in first],['Hello.']);self.assertFalse(second)

if __name__=='__main__':unittest.main()
