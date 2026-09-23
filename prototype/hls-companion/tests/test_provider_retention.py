import unittest
from companion.providers.bounded import RecentIds, NumericTombstones
from companion.providers.asr_assemblyai_streaming import AssemblyAIStreamingASRProvider, _AssemblyAIStream
from companion.providers.asr_speechmatics_realtime import SpeechmaticsRealtimeASRProvider, _SpeechmaticsStream
from companion.providers.base import SourceLanguagePolicy

class ProviderRetentionTests(unittest.TestCase):
    def test_recent_ids_are_bounded(self):
        seen=RecentIds()
        for i in range(20000): seen.add(str(i))
        self.assertEqual(len(seen),512)
        self.assertIn('19999',seen)
    def test_repeated_full_history_stays_retired(self):
        seen=NumericTombstones()
        for i in range(20000): seen.add(str(i))
        for i in range(20000): self.assertIn(str(i),seen)
        self.assertEqual(len(seen),512)
    def test_assembly_fallback_timestamp_is_seconds(self):
        p=AssemblyAIStreamingASRProvider({'id':'x','model':'universal-3-5-pro','baseUrl':'ws://localhost'})
        s=_AssemblyAIStream(p,SourceLanguagePolicy.specified('en'),16000)
        s._map_event({'type':'SpeechStarted','timestamp':6000})
        events=s._map_event({'type':'Turn','turn_order':1,'transcript':'hello','end_of_turn':True,'turn_is_formatted':True})
        self.assertEqual(events[0].begin_pcm,6.)
    def test_speechmatics_endpoint_is_not_ignored(self):
        p=SpeechmaticsRealtimeASRProvider({'id':'x','model':'enhanced','baseUrl':'ws://localhost'})
        s=_SpeechmaticsStream(p,SourceLanguagePolicy.specified('en'),16000)
        s._map_event({'message':'AddPartialTranscript','metadata':{'start_time':1,'end_time':2,'transcript':'hello'}})
        result=s._map_event({'message':'EndOfUtterance','metadata':{'start_time':2.,'end_time':2.}})
        self.assertEqual(result[0].caption_observation.kind,'endpoint')
        self.assertEqual(result[0].end_pcm,2.)
if __name__=='__main__': unittest.main()
