import asyncio,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from companion.message_translator import MessageTranslator
from companion.live_messages import LiveMessageStore
from companion.providers.base import TranslationResult
class BatchTests(unittest.IsolatedAsyncioTestCase):
 async def run_batch(self, broken=False):
  class Provider:
   id='batch'; calls=0
   capabilities=SimpleNamespace(language=SimpleNamespace(open_world_prompting=True))
   async def translate(self,request):
    self.calls+=1
    assert request.purpose=='live_chat_batch' and request.history==[]
    items=json.loads(request.source_text)
    self.size=len(items)
    values=[{'id':item['id'],'text':'译文'+item['text']} for item in reversed(items)]
    if broken: values[0]['id']=values[1]['id']
    return TranslationResult(json.dumps(values),self.id,0,{})
  provider=Provider(); store=LiveMessageStore(); pipeline=MessageTranslator(store,provider,concurrency=2)
  await pipeline.start(); pipeline.set_enabled(True)
  messages=[]
  for i in range(15):
   m=store.add(platform='youtube',source_id=str(i),author={},text='line'+str(i),media_time=i,translation_enabled=True)
   messages.append(m);pipeline.enqueue(m)
  for _ in range(200):
   if all(m.translation_state in {'done','failed'} for m in messages):break
   await asyncio.sleep(.01)
  await pipeline.stop()
  return provider,messages
 async def test_one_request_for_burst_and_reordered_ids_are_mapped(self):
  p,messages=await self.run_batch()
  self.assertEqual(p.calls,1);self.assertEqual(p.size,15)
  for i,m in enumerate(messages):self.assertEqual(m.translation,'译文line'+str(i))
 async def test_duplicate_ids_retry_and_never_publish_wrong_translations(self):
  p,messages=await self.run_batch(True)
  self.assertEqual(p.calls,3)
  self.assertTrue(all(m.translation_state=='failed' and m.translation is None for m in messages))
if __name__=='__main__':unittest.main()
