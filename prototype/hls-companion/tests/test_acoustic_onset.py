import unittest
from array import array
from companion.acoustic_onset import AcousticOnsetIndex

class AcousticOnsetTests(unittest.TestCase):
    def index(self, data, rate=16000, split=3200):
        index=AcousticOnsetIndex(rate)
        for offset in range(0,len(data),split):
            index.feed(data[offset:offset+split],offset/(rate*2))
        return index
    def test_silence_padding_and_late_token_onsets_use_the_same_audio_boundary(self):
        data=array('h',[0]*32000+[1000]*16000).tobytes()
        index=self.index(data)
        self.assertAlmostEqual(index.refine(1.85,3),2,places=6)
        self.assertAlmostEqual(index.refine(2.18,3),2,places=6)
    def test_noise_or_continuous_speech_keeps_provider_timing(self):
        index=self.index(array('h',[300]*48000).tobytes())
        self.assertEqual(index.refine(1.85,3),1.85)
    def test_no_repair_of_large_timestamp_errors_or_missing_audio(self):
        index=self.index(array('h',[0]*32000+[1000]*16000).tobytes())
        self.assertEqual(index.refine(3.1,4),3.1)
        self.assertEqual(AcousticOnsetIndex(16000).refine(2,3),2)
    def test_missing_samples_cannot_manufacture_a_quiet_boundary(self):
        index=AcousticOnsetIndex(16000)
        index.feed(bytes(3200),0)
        index.feed(array('h',[1000]*1600).tobytes(),2)
        self.assertEqual(index.refine(1.85,3),1.85)
    def test_24khz_partial_frames_do_not_drift(self):
        index=self.index(array('h',[0]*48000+[1000]*24000).tobytes(),24000,3200)
        self.assertAlmostEqual(index.refine(2.18,3),2,places=6)
    def test_history_is_bounded_and_pcm_is_not_retained(self):
        index=AcousticOnsetIndex(16000)
        for second in range(130):index.feed(bytes(32000),second)
        self.assertEqual(len(index.frames),12000)
        self.assertLess(len(index._tail),index.frame_bytes)
    def test_short_transient_is_not_speech_onset_evidence(self):
        data=array('h',[0]*32000+[1000]*320+[0]*16000).tobytes()
        self.assertEqual(self.index(data).refine(1.85,3),1.85)

if __name__=='__main__':unittest.main()
