import sys,unittest,asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_live_messages import _FakeHttpSession,_FakeWs
from companion.bilibili_danmaku_ingest import BilibiliDanmakuIngest,encode_packet,OP_ENTER_ROOM_REPLY
from companion.live_messages import LiveMessageStore
from companion.auth_lease import SessionAuthLease
from companion.core import DevelopmentBrowserProfileFallback
class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_closed_auth_is_reported_without_killing_background_task(self):
  lease=SessionAuthLease(DevelopmentBrowserProfileFallback(None)); lease.release_initial()
  x=BilibiliDanmakuIngest('https://live.bilibili.com/30858592',LiveMessageStore(),auth_lease=lease)
  await x.start()
  try:
   await asyncio.sleep(.02)
   self.assertFalse(x._task.done())
   self.assertEqual(x.status()['state'],'reconnecting')
   self.assertIn('closed authentication lease',x.status()['lastError'])
  finally:
   if x._task.done(): x._task.exception(); x._task=None
   await x.stop()

 async def test_cookie_reaches_both_http_requests(self):
  x=BilibiliDanmakuIngest('https://live.bilibili.com/30858592',LiveMessageStore())
  session=_FakeHttpSession(_FakeWs([])); x._session=session
  await x._resolve_connection('SESSDATA=test-only')
  self.assertEqual(len(session.get_calls),2)
  for _,kw in session.get_calls:
   self.assertEqual(kw['headers']['Cookie'],'SESSDATA=test-only')
   self.assertEqual(kw['headers']['Accept-Encoding'],'identity')
 def test_authentication_must_succeed(self):
  x=BilibiliDanmakuIngest('https://live.bilibili.com/30858592',LiveMessageStore())
  with self.assertRaises(RuntimeError): x.feed_bytes_for_test(encode_packet(OP_ENTER_ROOM_REPLY,b'{"code":-101}'))
  self.assertFalse(x.status()['connected'])
  x.feed_bytes_for_test(encode_packet(OP_ENTER_ROOM_REPLY,b'{"code":0}'))
  self.assertTrue(x.status()['connected'])
 async def test_poll_failure_backs_off_and_success_resets(self):
  x=BilibiliDanmakuIngest('https://live.bilibili.com/30858592',LiveMessageStore())
  outcomes=iter([500,500,200]); waits=[]
  class Response:
   async def __aenter__(self): return self
   async def __aexit__(self,*a): pass
   async def json(self): return {'code':0,'data':{'room':[]}}
  class Session:
   def get(self,*a,**kw):
    r=Response(); r.status=next(outcomes); return r
  async def sleep(delay):
   waits.append(delay)
   if len(waits)==3: x._running=False
  x._session=Session(); x._running=True
  with patch('companion.bilibili_danmaku_ingest.asyncio.sleep',side_effect=sleep):
   await x._poll_ajax_loop(30858592)
  self.assertEqual(waits,[2.0,4.0,1.0])
if __name__=='__main__': unittest.main()
