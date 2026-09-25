import json
import unittest
from types import SimpleNamespace
from companion.server import CompanionApplication
from companion.subtitle_store import CueStore
from companion.caption_chunker import CaptionChunker
from companion.providers.base import CaptionObservation, RecognitionToken

class DraftRequestTests(unittest.IsolatedAsyncioTestCase):
    async def request_case(self, identity):
        seen=[];app=object.__new__(CompanionApplication)
        app._subtitle_status=lambda:{}
        app.session=SimpleNamespace(media_session_id='current')
        app.subtitle_store=CueStore()
        app.subtitle_pipeline=SimpleNamespace(set_viewer_wall_time=lambda t:seen.append(t),
            caption_draft=lambda:{'seenPlayhead':seen[-1] if seen else None})
        response=await app.handle_subtitles(SimpleNamespace(query={
            'afterSeq':'0','playhead':'1700000002.5','mediaSessionId':identity}))
        return seen,json.loads(response.text)
    async def test_draft_uses_current_request_playhead_before_projection(self):
        seen,data=await self.request_case('current')
        self.assertEqual(seen,['1700000002.5'])
        self.assertEqual(data['draft']['seenPlayhead'],'1700000002.5')
    async def test_previous_media_generation_cannot_steer_the_new_draft(self):
        seen,data=await self.request_case('previous')
        self.assertEqual(seen,[])

class DraftLaneTests(unittest.TestCase):
    def test_a_future_speaker_does_not_hide_the_lane_being_heard(self):
        c=CaptionChunker(realtime=True,segments_only=True)
        for item,start,speaker,now in [('heard',1,'A',0),('future',10,'B',1)]:
            c.observe(CaptionObservation('stable_token_delta',1,item,tokens=(
                RecognitionToken(item,start,start+1,True,language='en',speaker=speaker),)),now=now)
        pending=c.pending_caption(1.5)
        self.assertIsNotNone(pending)
        self.assertEqual(pending.item_id,'heard')

if __name__=='__main__':unittest.main()
