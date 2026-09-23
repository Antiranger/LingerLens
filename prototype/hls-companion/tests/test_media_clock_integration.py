"""Real local FFmpeg + TCP pumps + production HLS flags. No internet."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from companion.core import build_ffmpeg_command, QualityOption, SelectedInputs
from companion.ytdlp_ingest import _TcpPump
from companion.source_timeline import MpegTsPtsProbe

FFMPEG=os.environ.get('LINGERLENS_FFMPEG') or shutil.which('ffmpeg')
FFPROBE=os.environ.get('LINGERLENS_FFPROBE') or shutil.which('ffprobe')

def invoke(args,cwd=None):
    p=subprocess.run(args,cwd=cwd,capture_output=True,timeout=30)
    if p.returncode: raise AssertionError(p.stderr.decode('utf-8','replace')[-3000:])
    return p.stdout

@unittest.skipUnless(FFMPEG and FFPROBE,'FFmpeg and FFprobe required')
class MediaClockIntegrationTests(unittest.TestCase):
    def test_independent_origins_and_video_gap_survive_real_packaging(self):
        with tempfile.TemporaryDirectory(prefix='lingerlens-clock-') as temp:
            folder=Path(temp); video=folder/'video.ts'; audio=folder/'audio.ts'; out=folder/'hls'; out.mkdir()
            common=[FFMPEG,'-hide_banner','-loglevel','error','-nostdin']
            invoke(common+['-f','lavfi','-i','testsrc2=size=160x90:rate=25:duration=6',
                '-vf',r'setpts=PTS+if(gte(T\,3)\,2/TB\,0)+100/TB','-an','-c:v','libx264',
                '-preset','ultrafast','-bf','2','-g','25','-fps_mode','passthrough',
                '-copyts','-muxdelay','0','-f','mpegts',str(video)])
            invoke(common+['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=6',
                '-af','asetpts=PTS+100.7/TB','-vn','-c:a','aac','-copyts','-muxdelay','0',
                '-f','mpegts',str(audio)])
            def packets(path):
                return json.loads(invoke([FFPROBE,'-v','error','-show_packets','-show_entries',
                    'packet=codec_type,pts_time,dts_time','-of','json',str(path)]))['packets']
            vp,ap=packets(video),packets(audio)
            expected=float(ap[0]['pts_time'])-float(vp[0]['pts_time'])
            # The actual H.264 B-frame stream exercises PTS+DTS probe support.
            probe=MpegTsPtsProbe(want_audio=False)
            self.assertTrue(probe.feed(video.read_bytes()))
            self.assertTrue(probe.clock_valid)
            pumps=[_TcpPump('video'),_TcpPump('audio')]
            try:
                pumps[0].start(io.BytesIO(video.read_bytes())); pumps[1].start(io.BytesIO(audio.read_bytes()))
                quality=QualityOption('test','test',160,90,25,'avc1','mp4a',True,False,1,'v','a')
                selected=SelectedInputs(quality,'https://invalid/video','https://invalid/audio',{}, {})
                command=build_ffmpeg_command(selected,out,ffmpeg=FFMPEG,input_urls=[p.url for p in pumps])
                invoke(command,cwd=out)
                packaged=packets(out/'live.m3u8')
                v=sorted(float(p['pts_time']) for p in packaged if p['codec_type']=='video')
                a=sorted(float(p['pts_time']) for p in packaged if p['codec_type']=='audio')
                print("CLOCK_RESULT", json.dumps({"inputOriginDelta":expected,"outputOriginDelta":a[0]-v[0],"videoGap":max(y-x for x,y in zip(v,v[1:]))}))
                self.assertAlmostEqual(a[0]-v[0],expected,delta=0.06)
                self.assertGreater(max(y-x for x,y in zip(v,v[1:])),1.9)
                self.assertLess(max(y-x for x,y in zip(a,a[1:])),0.06)
                self.assertIn('-isync',command)
                self.assertNotEqual(command[command.index('-max_interleave_delta')+1],'0')
            finally:
                for pump in pumps: pump.stop(timeout=1)

if __name__=='__main__': unittest.main()
