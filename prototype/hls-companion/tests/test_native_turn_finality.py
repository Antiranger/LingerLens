import unittest
from companion.providers.asr_dashscope_livetranslate import QwenLiveTranslateASRProvider, _LiveTranslateStream
from companion.providers.base import SourceLanguagePolicy
from companion.caption_chunker import CaptionChunker

class NativeTurnLifecycleTests(unittest.TestCase):
    def stream(self):
        p=QwenLiveTranslateASRProvider({'id':'fixture','model':'qwen3.5-livetranslate-flash-realtime',
            'baseUrl':'wss://example.invalid','options':{}})
        return _LiveTranslateStream(p,SourceLanguagePolicy.specified('en'),16000)
    def test_translation_completion_cannot_close_an_untranscribed_source(self):
        s=self.stream();c=CaptionChunker(realtime=True,segments_only=True);c.reset(0)
        frames=[{'type':'input_audio_buffer.speech_started','item_id':'a','audio_start_ms':2000},
          {'type':'response.created','response':{'id':'r1'}},
          {'type':'response.text.done','response_id':'r1','text':'完整翻译。'},
          {'type':'conversation.item.input_audio_transcription.completed','item_id':'a','transcript':'Complete source.'}]
        events=[];chunks=[]
        for raw in frames:
            mapped=s._map_event(raw);events+=mapped
            for e in mapped:
                if e.caption_observation: chunks+=list(c.observe(e.caption_observation,now=4).chunks)
        self.assertEqual([x.text for x in chunks],['Complete source.'])
        final=[e for e in events if e.type=='final']
        self.assertEqual(len(final),1)
        self.assertEqual((final[0].text,final[0].translation),('Complete source.','完整翻译。'))
    def test_late_completion_is_routed_by_response_id_not_current_response(self):
        s=self.stream()
        for item,rid,text in [('a','r1','First.'),('b','r2','Second.')]:
            for raw in ({'type':'input_audio_buffer.speech_started','item_id':item},
                {'type':'response.created','response':{'id':rid}},
                {'type':'conversation.item.input_audio_transcription.completed','item_id':item,'transcript':text}):
                s._map_event(raw)
        e=s._map_event({'type':'response.text.done','response_id':'r1','text':'第一句。'})
        self.assertEqual(e[-1].item_id,'a')
        self.assertEqual(e[-1].text,'First.')
        e=s._map_event({'type':'response.text.done','response_id':'r2','text':'第二句。'})
        self.assertEqual(e[-1].item_id,'b')

if __name__=='__main__':unittest.main()
