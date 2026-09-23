"""Binary clock/production command regressions; no public network."""
import pathlib
import unittest
from companion.source_timeline import MpegTsPtsProbe
from companion.server import CompanionApplication
from companion import core

def timestamp(ticks,prefix):
    return bytes([prefix|((ticks>>29)&14)|1,(ticks>>22)&255,((ticks>>14)&254)|1,(ticks>>7)&255,((ticks<<1)&254)|1])

def packet(pts=90000,dts=None,stream=0xe0):
    flags=0x80 if dts is None else 0xc0
    stamps=timestamp(pts,0x20 if dts is None else 0x30)
    if dts is not None: stamps+=timestamp(dts,0x10)
    raw=bytes([0x47,0x41,0,0x10,0,0,1,stream,0,0,0x80,flags,len(stamps)])+stamps
    return raw+bytes([255])*(188-len(raw))

class MediaHardeningTests(unittest.TestCase):
    def test_pts_and_dts_are_both_legal(self):
        probe=MpegTsPtsProbe(want_audio=False)
        self.assertEqual(probe.feed(packet(90000,86400)),[1.0])

    def test_bad_pts_marker_is_rejected(self):
        data=bytearray(packet()); data[13]&=254
        self.assertEqual(MpegTsPtsProbe(want_audio=False).feed(bytes(data)),[])

    def test_private_metadata_does_not_claim_the_video_pid(self):
        probe=MpegTsPtsProbe(want_audio=False)
        self.assertEqual(probe.feed(packet(stream=0xbd)),[])
        self.assertEqual(probe.feed(packet()),[1.0])

    def test_adaptation_only_discontinuity_invalidates_selected_clock(self):
        probe=MpegTsPtsProbe(want_audio=False); probe.feed(packet())
        probe.feed(bytes([0x47,1,0,0x20,183,0x80])+bytes(182))
        self.assertFalse(probe.clock_valid)

    def test_fragmented_input_and_noise_recover_sync(self):
        data=b'not transport packets'*21+packet()+packet(180000)+packet(270000)
        probe=MpegTsPtsProbe(want_audio=False); values=[]
        for pos in range(0,len(data),71): values.extend(probe.feed(data[pos:pos+71]))
        self.assertEqual(values,[1.,2.,3.])

    def test_measured_invalid_clock_triggers_recovery_but_missing_clock_does_not(self):
        status={'state':'running','playlistReady':True,'targetDuration':2}
        live={'running':True,'sourceIdleSeconds':0.1,'sourceClockValid':False}
        self.assertIsNotNone(CompanionApplication._session_recovery_reason(status,{**live,'sourceClockReason':'transport-discontinuity'}))
        self.assertIsNone(CompanionApplication._session_recovery_reason(status,{**live,'sourceClockReason':'no-pts-probe'}))

    def test_packaging_does_not_independently_delete_source_gaps(self):
        for value in (core._VIDEO_SETTS,core._AUDIO_SETTS):
            self.assertNotIn('PREV_OUTDTS+',value)
            self.assertNotIn('PREV_OUTPTS+',value)

if __name__ == '__main__': unittest.main()
