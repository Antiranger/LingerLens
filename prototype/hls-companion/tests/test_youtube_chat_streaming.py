import sys,asyncio,tempfile,unittest,json
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from companion.youtube_chat_ingest import YouTubeChatIngest
from companion.live_messages import LiveMessageStore
from companion.capture_clock import CaptureClock
from companion.auth_lease import SessionAuthLease
from companion.core import DevelopmentBrowserProfileFallback
class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_live_message_older_than_local_connect_is_not_discarded(self):
  clock=CaptureClock()
  clock.update(pdt_epoch=1000.0, completed_private_media_seconds=20.0, target_duration=2.0, monotonic_time=50.0)
  store=LiveMessageStore(clock=clock); source=YouTubeChatIngest('https://youtube.com/watch?v=x',store,clock=clock)
  source.source_started_wall_time=100.0
  action={'addChatItemAction':{'item':{'liveChatTextMessageRenderer':{
   'id':'late','message':{'simpleText':'hello'},'timestampUsec':'95000000',
   'authorName':{'simpleText':'viewer'},
  }}}}
  source.consume_json(action,received_monotonic=51.0,received_at=101.0)
  self.assertEqual(source.status()['received'],1)
  self.assertEqual(store.stats()['pendingClock'],0)

 async def test_spawn_process_detaches_stdin_from_desktop_control_pipe(self):
  source=YouTubeChatIngest('https://youtube.com/watch?v=x',LiveMessageStore())
  captured={}
  class FakeProcess:
   returncode=None
  async def fake_spawn(*command, **kwargs):
   captured.update(command=command, kwargs=kwargs)
   return FakeProcess()
  with patch('companion.youtube_chat_ingest.asyncio.create_subprocess_exec', fake_spawn):
   process=await source._spawn_process('yt-dlp','--skip-download')
  self.assertIsInstance(process, FakeProcess)
  self.assertIs(captured['kwargs']['stdin'], asyncio.subprocess.DEVNULL)
  self.assertIs(captured['kwargs']['stdout'], asyncio.subprocess.PIPE)
  self.assertIs(captured['kwargs']['stderr'], asyncio.subprocess.STDOUT)

 async def test_complete_raw_response_is_consumed_before_jsonl_and_deduped(self):
  store=LiveMessageStore(); got=[]
  source=YouTubeChatIngest('https://youtube.com/watch?v=x',store,on_message=got.append)
  action={'addChatItemAction':{'item':{'liveChatTextMessageRenderer':{'id':'early','message':{'simpleText':'hello'}}}}}
  raw=json.dumps({'continuationContents':{'liveChatContinuation':{'actions':[action],'continuations':[{'timedContinuationData':{'timeoutMs':10000}}]}}}).encode()
  with tempfile.TemporaryDirectory() as directory:
   fragment=Path(directory)/'chat.live_chat.json.part-Frag2'
   fragment.write_bytes(raw[:40])
   source._process=type('Proc',(),{'returncode':None,'stdout':None})()
   source._running=True
   task=asyncio.create_task(source._consume_process(Path(directory)))
   try:
    await asyncio.sleep(.08);self.assertEqual(len(got),0)
    fragment.write_bytes(raw)
    await asyncio.sleep(.12);self.assertEqual(len(got),1)
    (Path(directory)/'chat.live_chat.json.part').write_text(json.dumps(action)+'\n',encoding='utf-8')
    await asyncio.sleep(.12);self.assertEqual(len(got),1)
   finally: source._running=False;await task

 async def test_closed_auth_reports_error_instead_of_silently_killing_task(self):
  lease=SessionAuthLease(DevelopmentBrowserProfileFallback(None))
  lease.release_initial()
  source=YouTubeChatIngest('https://youtube.com/watch?v=x',LiveMessageStore(),auth_lease=lease)
  await source.start()
  try:
   await asyncio.sleep(.02)
   self.assertFalse(source._task.done())
   self.assertEqual(source.status()['state'],'reconnecting')
   self.assertIn('closed authentication lease',source.status()['lastError'])
   self.assertFalse(source.status()['connected'])
  finally: await source.stop()

 async def test_split_line_in_part_and_ignore_fragment(self):
  source=YouTubeChatIngest('https://youtube.com/watch?v=x',LiveMessageStore(),proxy='http://127.0.0.1:7890')
  data=json.dumps({'addChatItemAction':{'item':{'liveChatTextMessageRenderer':{'id':'one','message':{'simpleText':'hello'}}}}}).encode()+b'\n'
  with tempfile.TemporaryDirectory() as directory:
   f=Path(directory)/'chat.live_chat.json.part'; f.write_bytes(data[:30])
   (Path(directory)/'chat.live_chat.json.part-Frag1').write_bytes(data)
   source._running=True
   class Proc:
    returncode=None; stdout=None
   source._process=Proc()
   task=asyncio.create_task(source._consume_process(Path(directory)))
   await asyncio.sleep(.08)
   self.assertEqual(source._received,0)
   with f.open('ab') as h: h.write(data[30:])
   await asyncio.sleep(.08)
   source._running=False; await task
   self.assertEqual(source._received,1)
  cmd=source.command(Path('test'),[])
  self.assertIn('--proxy',cmd); self.assertIn('--no-progress',cmd)
if __name__=='__main__': unittest.main()

