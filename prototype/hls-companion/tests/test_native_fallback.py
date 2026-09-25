import time
import unittest
from companion.providers.base import (StreamMeta, TranslationCapabilities,
    TranslationProvider, TranslationRequest, TranslationResult, ProviderRefusalError)
from companion.providers.native_session import NativeSessionTranslation, NativeTranslationBus

class RecordingFallback(TranslationProvider):
    id='configured-mt';label='fixture';model='fixture'
    def __init__(self):self.requests=[]
    @property
    def capabilities(self):return TranslationCapabilities(True,True,False,False,4096)
    async def translate(self,request):
        self.requests.append(request)
        return TranslationResult('exact cue translation',self.id,1,{'input_tokens':2})

class NativeFallbackTests(unittest.IsolatedAsyncioTestCase):
    def request(self,text='first half',**kw):
        return TranslationRequest(text,StreamMeta(None,None,None,'en','zh-Hans'),[],[],
            deadline_monotonic=time.monotonic()+5,item_id='item',**kw)
    def provider(self,bus,fallback=None):
        return NativeSessionTranslation(bus,provider_id='native',label='native',model='fixture',fallback=fallback)
    async def test_cut_uses_explicit_fallback_immediately_with_original_deadline_and_usage(self):
        bus=NativeTranslationBus();bus.record(item_id='item',source_text='first half second half',translation='partial')
        fallback=RecordingFallback();request=self.request(cut_reason='hard_deadline')
        started=time.monotonic();result=await self.provider(bus,fallback).translate(request)
        self.assertLess(time.monotonic()-started,.2)
        self.assertIs(fallback.requests[0],request)
        self.assertEqual((result.provider_id,result.usage),('configured-mt',{'input_tokens':2}))
    async def test_completed_matching_native_segment_never_causes_a_second_paid_call(self):
        bus=NativeTranslationBus();bus.close_item('item',source_text='first half',translation='native answer')
        fallback=RecordingFallback();result=await self.provider(bus,fallback).translate(self.request())
        self.assertEqual(result.text,'native answer');self.assertEqual(fallback.requests,[])
    async def test_closed_unaligned_source_is_translated_as_its_own_cue_not_a_guessed_prefix(self):
        bus=NativeTranslationBus();bus.close_item('item',source_text='first half second half',translation='whole paragraph')
        fallback=RecordingFallback();result=await self.provider(bus,fallback).translate(self.request())
        self.assertEqual(result.text,'exact cue translation')
        self.assertEqual(fallback.requests[0].source_text,'first half')
    async def test_missing_fallback_does_not_create_network_work(self):
        bus=NativeTranslationBus();bus.close_item('item',source_text='other',translation='not this cue')
        with self.assertRaises(ProviderRefusalError):await self.provider(bus).translate(self.request())
    async def test_obsolete_generation_never_spends_fallback_tokens(self):
        bus=NativeTranslationBus();bus.reset(2);fallback=RecordingFallback()
        with self.assertRaises(ProviderRefusalError):await self.provider(bus,fallback).translate(self.request(generation=1,cut_reason='hard_deadline'))
        self.assertEqual(fallback.requests,[])

if __name__=='__main__':unittest.main()
