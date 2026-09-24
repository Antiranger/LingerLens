"""Qwen source and response finality are independent, with protocol item links."""
import unittest
from companion.providers.asr_dashscope_livetranslate import QwenLiveTranslateASRProvider, _LiveTranslateStream
from companion.providers.base import SourceLanguagePolicy
from companion.caption_chunker import CaptionChunker

class NativeTurnLifecycleTests(unittest.TestCase):
    def stream(self):
        provider=QwenLiveTranslateASRProvider({'id':'fixture','model':'qwen3.5-livetranslate-flash-realtime','baseUrl':'wss://example.invalid','options':{}})
        return _LiveTranslateStream(provider,SourceLanguagePolicy.specified('en'),16000)
    def linked(self, stream, item, response):
        for frame in ({'type':'response.created','response':{'id':response}},
            {'type':'conversation.item.created','previous_item_id':item,'item':{'id':'out-'+response,'role':'assistant'}},
            {'type':'response.output_item.added','response_id':response,'item':{'id':'out-'+response,'role':'assistant'}}):
            stream._map_event(frame)
    def complete(self, stream, response, text):
        events=stream._map_event({'type':'response.text.done','response_id':response,'text':text})
        events+=stream._map_event({'type':'response.done','response':{'id':response,'status':'completed'}})
        return events
    def test_translation_completion_cannot_close_an_untranscribed_source(self):
        s=self.stream();c=CaptionChunker(realtime=True,segments_only=True);c.reset(0)
        s._map_event({'type':'input_audio_buffer.speech_started','item_id':'a','audio_start_ms':2000})
        self.linked(s,'a','r1')
        events=self.complete(s,'r1','完整翻译。')
        self.assertFalse(any(e.type=='final' for e in events))
        events+=s._map_event({'type':'conversation.item.input_audio_transcription.completed','item_id':'a','transcript':'Complete source.'})
        chunks=[]
        for e in events:
            if e.caption_observation:chunks+=list(c.observe(e.caption_observation,now=4).chunks)
        self.assertEqual([x.text for x in chunks],['Complete source.'])
        final=[e for e in events if e.type=='final']
        self.assertEqual(len(final),1)
        self.assertEqual((final[0].text,final[0].translation),('Complete source.','完整翻译。'))
    def test_late_completion_is_routed_by_response_id_not_current_response(self):
        s=self.stream()
        for item,rid,text in [('a','r1','First.'),('b','r2','Second.')]:
            s._map_event({'type':'input_audio_buffer.speech_started','item_id':item})
            self.linked(s,item,rid)
            s._map_event({'type':'conversation.item.input_audio_transcription.completed','item_id':item,'transcript':text})
        first=[e for e in self.complete(s,'r1','第一句。') if e.type=='final']
        second=[e for e in self.complete(s,'r2','第二句。') if e.type=='final']
        self.assertEqual((first[0].item_id,first[0].text),('a','First.'))
        self.assertEqual((second[0].item_id,second[0].text),('b','Second.'))

if __name__=='__main__':unittest.main()
