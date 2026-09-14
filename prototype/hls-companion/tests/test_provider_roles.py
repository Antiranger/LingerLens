import asyncio,copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from companion.providers.config import DEFAULT_CONFIG,atomic_write_config,load_config,update_config,update_model_settings
from companion.providers.base import StreamMeta,TranslationResult,TranslationRequest
from companion.message_translator import MessageTranslator
from companion.live_messages import LiveMessageStore
from companion.translation_prompt import build_translation_instruction
class ConfigTests(unittest.TestCase):
 def test_independent_roles_persist_and_protect_references(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'p.json'; c=copy.deepcopy(DEFAULT_CONFIG); row=copy.deepcopy(c['translation']['providers'][0]); row['id']='chat'; c['translation']['providers'].append(row); atomic_write_config(p,c)
   first=load_config(p); self.assertEqual(first['chatTranslation']['active'],c['translation']['active'])
   changed=update_config(p,{'chatTranslation':{'active':'chat'}})
   self.assertEqual(changed['translation']['active'],c['translation']['active']); self.assertEqual(load_config(p)['chatTranslation']['active'],'chat')
   changed['translation']['providers']=[changed['translation']['providers'][0]]
   with self.assertRaisesRegex(ValueError,'chatTranslation'): update_model_settings(p,changed)
 def test_chat_prompt_ignores_history(self):
  r=TranslationRequest('hello',StreamMeta(None,None,None,'en','zh-Hans'),[('SECRET_HISTORY','old')],[],purpose='live_chat')
  prompt=build_translation_instruction(r)
  self.assertEqual(prompt.user_text,'hello'); self.assertNotIn('SECRET_HISTORY',prompt.system_text+prompt.user_text); self.assertIn('弹幕',prompt.system_text)
class WorkerTests(unittest.IsolatedAsyncioTestCase):
 async def test_concurrency_cache_and_no_context(self):
  class Provider:
   id='test'; model='test'
   calls=0; active=0; peak=0
   async def translate(self,r):
    self.calls+=1; self.active+=1; self.peak=max(self.peak,self.active)
    assert r.history==[] and r.purpose=='live_chat'
    await asyncio.sleep(.02); self.active-=1
    return TranslationResult('translated',self.id,20,{})
  p=Provider(); st=LiveMessageStore(); x=MessageTranslator(st,p,concurrency=2)
  await x.start(); x.set_enabled(True)
  def add(i,text):
   m=st.add(platform='youtube',source_id=str(i),author={},text=text,media_time=float(i),translation_enabled=True); x.enqueue(m); return m
  a=add(1,'hello'); b=add(2,'world')
  for _ in range(100):
   if b.translation_state=='done': break
   await asyncio.sleep(.005)
  c=add(3,'hello')
  for _ in range(100):
   if c.translation_state=='done': break
   await asyncio.sleep(.005)
  await x.stop()
  self.assertEqual(p.peak,2); self.assertEqual(p.calls,2); self.assertEqual(c.translation,'translated')
 async def test_old_language_not_published(self):
  gate=asyncio.Event(); entered=asyncio.Event()
  class Provider:
   id='test'; calls=0
   async def translate(self,r):
    self.calls+=1; entered.set(); await gate.wait()
    return TranslationResult('translated',self.id,0,{})
  p=Provider(); st=LiveMessageStore(); x=MessageTranslator(st,p); await x.start(); x.set_enabled(True)
  m=st.add(platform='youtube',source_id='one',author={},text='hello',media_time=1,translation_enabled=True); x.enqueue(m)
  await entered.wait(); x.reconfigure(p,StreamMeta(None,None,None,'en','ja')); gate.set(); await asyncio.sleep(.02); await x.stop()
  self.assertEqual(m.translation_state,'skipped'); self.assertIsNone(m.translation)
 async def test_empty_retry_cap_and_deadline(self):
  class Provider:
   id='empty'; calls=0
   async def translate(self,r):
    self.calls+=1
    return TranslationResult('',self.id,0,{})
  for timeout, expected in [(2,3),(.05,1)]:
   p=Provider(); st=LiveMessageStore(); x=MessageTranslator(st,p,timeout_seconds=timeout)
   await x.start(); x.set_enabled(True)
   m=st.add(platform='youtube',source_id='retry',author={},text='hello',media_time=1,translation_enabled=True); x.enqueue(m)
   for _ in range(200):
    if m.translation_state=='failed': break
    await asyncio.sleep(.01)
   await x.stop(); self.assertEqual(p.calls,expected); self.assertEqual(m.translation_state,'failed')
 async def test_role_switch_only_replaces_requested_runtime(self):
  import argparse
  from companion.server import CompanionApplication
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   app=CompanionApplication(argparse.Namespace(runtime_dir=root/'media',providers_file=root/'providers.json',publish_delay=2,cookies_from_browser=None))
   config=load_config(app.providers_path); extra=copy.deepcopy(config['translation']['providers'][0]); extra['id']='chat-fast'; config['translation']['providers'].append(extra); atomic_write_config(app.providers_path,config)
   old=SimpleNamespace(id='old')
   app.subtitle_pipeline=SimpleNamespace(translation_provider=old,fallback_translation_provider=None)
   app.message_translator=MessageTranslator(app.message_store,old)
   class Request:
    async def json(self): return {'chatTranslation':{'active':'chat-fast'}}
   response=await app.handle_update_providers(Request())
   self.assertEqual(response.status,200)
   self.assertIs(app.subtitle_pipeline.translation_provider,old)
   self.assertEqual(app.message_translator.translation_provider.id,'chat-fast')
if __name__=='__main__': unittest.main()
