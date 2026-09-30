"""Source availability must not wait for an onset-to-server round trip."""
import unittest
from companion.caption_chunker import CaptionChunker
from companion.providers.base import CaptionObservation, RecognitionToken, StreamMeta
from companion.providers.native_session import NativeTranslationBus
from companion.subtitle_pipeline import SubtitlePipeline
from companion.subtitle_store import CueStore
from companion.media_anchor import MediaAnchor
from test_subtitle_pipeline import FakeASR

class PreviewPrefetchTests(unittest.TestCase):
    def pipeline(self):
        p=SubtitlePipeline(asr_provider=FakeASR(),cue_store=CueStore(),
            meta=StreamMeta('test','test','test','en','zh-Hans'),native_translation_bus=NativeTranslationBus())
        p._running=True; p.media_epoch=1000
        p.caption_chunker.observe(CaptionObservation('stable_token_delta',p._generation,'future',
            tokens=(RecognitionToken('Upcoming speech',10,12,True,language='en'),)),now=0)
        return p
    def test_future_preview_is_preloaded_even_without_a_viewer_poll(self):
        p=self.pipeline()
        drafts=p.caption_drafts()
        self.assertEqual(len(drafts),1)
        self.assertEqual((drafts[0]['text'],drafts[0]['tStart'],drafts[0]['tEnd']),('Upcoming speech',1010,1012))
        self.assertEqual(drafts[0]['itemId'],'future')
    def test_prefetched_preview_carries_safe_native_translation_prefix(self):
        p=self.pipeline()
        p.native_translation_bus.record(item_id='future',source_text='Upcoming speech',translation='即将说话',
            anchors=(('Upcoming speech','即将说话'),),anchors_trusted=True)
        draft=p.caption_drafts()[0]
        self.assertEqual(draft['translation'],'即将说话')
        self.assertFalse(p.native_translation_bus._segments['future'].consumed)
    def test_untrusted_or_missing_anchor_never_fabricates_a_preview_time(self):
        p=self.pipeline();p.media_anchor=MediaAnchor()
        self.assertEqual(p.caption_drafts(),[])
    def test_future_lanes_are_bounded_and_do_not_close_the_chunker(self):
        c=CaptionChunker(realtime=True,segments_only=True)
        for n in range(40):
            c.observe(CaptionObservation('stable_token_delta',1,str(n),tokens=(
                RecognitionToken(str(n),n,n+1,True),)),now=0)
        previews=c.pending_captions()
        self.assertEqual(len(previews),32)
        self.assertEqual(previews[0].begin_pcm,0)
        self.assertEqual(c.telemetry().caption_chunks,0)
    def test_provider_item_identity_survives_immutable_cue_serialization(self):
        store=CueStore()
        cue=store.add(t_start=1,t_end=2,hold=1,src='Again',lang='en',timing_source='vad',item_id='a')
        self.assertEqual(cue.to_dict()['itemId'],'a')
        with self.assertRaises(ValueError):store.update(cue.id,item_id='b')

if __name__=='__main__':unittest.main()
