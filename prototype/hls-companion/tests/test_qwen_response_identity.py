"""Protocol-shaped identity/finality regressions; no network or credentials."""
import unittest
from companion.providers.asr_dashscope_livetranslate import QwenLiveTranslateASRProvider, _LiveTranslateStream
from companion.providers.base import SourceLanguagePolicy

class ResponseIdentityTests(unittest.TestCase):
    def setUp(self):
        provider = QwenLiveTranslateASRProvider({'id':'test','model':'qwen3.8-livetranslate-flash-realtime','baseUrl':'wss://example.invalid'})
        self.stream = _LiveTranslateStream(provider, SourceLanguagePolicy.specified('en'), 16000)
    def send(self, typ, **fields):
        return self.stream._map_event({'type':typ, **fields})
    def source(self, item, text):
        self.send('input_audio_buffer.speech_started', item_id=item, audio_start_ms=1000)
        return self.send('conversation.item.input_audio_transcription.completed', item_id=item, transcript=text)
    def link(self, rid, output, source):
        events=self.send('conversation.item.created', previous_item_id=source, item={'id':output,'role':'assistant'})
        return events+self.send('response.output_item.added', response_id=rid, item={'id':output,'role':'assistant'})
    def test_reversed_responses_follow_explicit_source_ids(self):
        self.source('a','First.'); self.source('b','Second.')
        self.send('response.created', response={'id':'rb'})
        self.link('rb','ob','b')
        self.send('response.text.done', response_id='rb', item_id='ob', text='第二句。')
        events=self.send('response.done', response={'id':'rb','status':'completed'})
        final=[e for e in events if e.type=='final']
        self.assertEqual([(e.item_id,e.text,e.translation) for e in final],[('b','Second.','第二句。')])
    def test_text_done_is_not_success_before_cancellation(self):
        self.source('a','First.')
        self.send('response.created',response={'id':'r'})
        self.link('r','o','a')
        events=self.send('response.text.done',response_id='r',item_id='o',text='不完整')
        self.assertFalse(any(e.type=='final' for e in events))
        events=self.send('response.done',response={'id':'r','status':'cancelled'})
        self.assertEqual([e.translation for e in events if e.type=='final'],[''])
    def test_output_link_can_arrive_after_translation_without_misjoining(self):
        self.source('a','First.'); self.source('b','Second.')
        self.send('response.created',response={'id':'r'})
        self.assertFalse(self.send('response.text.delta',response_id='r',item_id='o',delta='第二句。'))
        self.assertFalse(self.send('response.done',response={'id':'r','status':'completed'}))
        events=self.link('r','o','b')
        self.assertEqual([(e.item_id,e.translation) for e in events if e.type=='final'],[('b','第二句。')])
    def test_missing_join_never_falls_back_to_turn_fifo(self):
        self.source('a','First.')
        self.send('response.created',response={'id':'r'})
        events=self.send('response.text.done',response_id='r',item_id='unknown',text='未知')
        self.assertFalse(any(e.type=='final' for e in events))
    def test_response_complete_before_source_waits_for_source(self):
        self.send('input_audio_buffer.speech_started',item_id='a',audio_start_ms=1000)
        self.send('response.created',response={'id':'r'}); self.link('r','o','a')
        self.send('response.text.delta',response_id='r',item_id='o',delta='完整。')
        self.assertFalse(any(e.type=='final' for e in self.send('response.done',response={'id':'r','status':'completed'})))
        events=self.send('conversation.item.input_audio_transcription.completed',item_id='a',transcript='Complete.')
        self.assertEqual([(e.text,e.translation) for e in events if e.type=='final'],[('Complete.','完整。')])

if __name__=='__main__': unittest.main()
