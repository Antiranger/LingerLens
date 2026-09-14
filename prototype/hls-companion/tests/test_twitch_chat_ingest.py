import asyncio,sys,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from companion.twitch_chat_ingest import TwitchChatIngest
from companion.live_messages import LiveMessageStore
from aiohttp import WSMsgType
class CM:
 def __init__(self,x): self.x=x
 async def __aenter__(self): return self.x
 async def __aexit__(self,*args): pass
class Tests(unittest.IsolatedAsyncioTestCase):
 def test_system_all_proxy_is_used_by_production_constructor(self):
  with patch('companion.twitch_chat_ingest.getproxies',return_value={'all':'http://127.0.0.1:7890'}):
   source=TwitchChatIngest('https://twitch.tv/guanweiboy',LiveMessageStore())
  self.assertEqual(source.proxy,'http://127.0.0.1:7890')

 async def test_burst_storage_dedupe_and_stop(self):
  store=LiveMessageStore(); got=[]; states=[]
  source=TwitchChatIngest('https://twitch.tv/guanweiboy',store,on_message=got.append,proxy='http://127.0.0.1:7890')
  class WS:
   async def send_str(self,s): pass
   def __aiter__(self): return self.events()
   async def events(self):
    lines=[f'@id={i};user-id=1 :a!a@a PRIVMSG #guanweiboy :message {i}' for i in range(100)]
    yield SimpleNamespace(type=WSMsgType.TEXT,data='\r\n'.join(lines+lines))
    states.append(source.status())
    source._stop_event.set()
  class Session:
   def ws_connect(self,*a,**kw):
    assert kw['proxy']=='http://127.0.0.1:7890'
    return CM(WS())
  with patch('companion.twitch_chat_ingest.aiohttp.ClientSession',return_value=CM(Session())), patch('companion.twitch_chat_ingest.aiohttp.TCPConnector'):
   await source.start(); await source._task; await source.stop()
  self.assertEqual(source.received,100); self.assertEqual(len(got),100)
  self.assertEqual(len(store),100); self.assertIsNone(source._task)
  self.assertEqual(states[0]['state'],'running'); self.assertTrue(states[0]['connected'])
 async def test_clean_disconnect_also_backs_off(self):
  source=TwitchChatIngest('https://twitch.tv/test',LiveMessageStore())
  waits=[]
  class WS:
   async def send_str(self,s): pass
   def __aiter__(self): return self.events()
   async def events(self):
    if False: yield None
  class Session:
   def ws_connect(self,*a,**kw): return CM(WS())
  async def sleep(delay):
   waits.append(delay)
   if len(waits)==3: source._stop_event.set()
  with patch('companion.twitch_chat_ingest.aiohttp.ClientSession',return_value=CM(Session())), patch('companion.twitch_chat_ingest.aiohttp.TCPConnector'), patch('companion.twitch_chat_ingest.asyncio.sleep',side_effect=sleep):
   await source._run()
  self.assertEqual(waits,[1.0,1.5,2.25])
if __name__=='__main__': unittest.main()
