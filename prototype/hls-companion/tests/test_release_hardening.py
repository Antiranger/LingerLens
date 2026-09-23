"""Regression cases from the pre-release audit. No external network or keys."""
import copy
import base64
import hashlib
import hmac
import unittest
from urllib.parse import urlsplit, parse_qsl
from companion.providers import create_asr
from companion.providers.config import BUILTIN_ASR_PROVIDERS, DEFAULT_CONFIG, validate_config
from companion.providers.base import SourceLanguagePolicy
from companion.providers.asr_elevenlabs_scribe_realtime import _ElevenLabsScribeStream
from companion.providers.asr_tencent_asr import _TencentAsrStream
from companion.providers.native_session import NativeTranslationBus
from companion.caption_chunker import CaptionChunker

class ReleaseHardeningTests(unittest.TestCase):
    def test_every_builtin_roundtrips_validation(self):
        for preset in BUILTIN_ASR_PROVIDERS:
            with self.subTest(preset=preset['id']):
                cfg = copy.deepcopy(DEFAULT_CONFIG)
                cfg['asr'] = {'active': preset['id'], 'providers': [copy.deepcopy(preset)]}
                validate_config(cfg)
                self.assertIsNotNone(create_asr(preset))

    def test_scribe_two_idless_commits_are_two_captions(self):
        cfg = next(p for p in BUILTIN_ASR_PROVIDERS if p['kind']=='elevenlabs-scribe-realtime')
        stream = _ElevenLabsScribeStream(create_asr(cfg), SourceLanguagePolicy.specified('en'), 16000)
        chunker = CaptionChunker(realtime=True)
        chunks=[]
        for i,text in enumerate(('First sentence.', 'A different second sentence.')):
            event=stream._map_event({'message_type':'committed_transcript','text':text})
            obs=event.caption_observation
            import dataclasses
            obs=dataclasses.replace(obs,begin_pcm=i*3.,end_pcm=i*3.+2.)
            chunks.extend(chunker.observe(obs,now=i*3.).chunks)
        self.assertEqual([c.text for c in chunks],['First sentence.','A different second sentence.'])

    def test_scribe_repeated_words_are_not_globally_deduplicated(self):
        cfg = next(p for p in BUILTIN_ASR_PROVIDERS if p['kind']=='elevenlabs-scribe-realtime')
        stream = _ElevenLabsScribeStream(create_asr(cfg),SourceLanguagePolicy.specified('en'),16000)
        ids=[]
        for _ in range(2):
            event=stream._map_event({'message_type':'committed_transcript','text':'Yes.'})
            ids.append(event.item_id)
            self.assertIsNone(stream._map_event({'message_type':'committed_transcript_with_timestamps','text':'Yes.'}))
        self.assertNotEqual(*ids)

    def test_tencent_signs_resolved_path(self):
        cfg=copy.deepcopy(next(p for p in BUILTIN_ASR_PROVIDERS if p['kind']=='tencent-asr'))
        cfg['apiKey']='test-secret-not-real'
        cfg['options'].update(appId='123456', secretId='test-id')
        stream=_TencentAsrStream(create_asr(cfg),SourceLanguagePolicy.specified('zh'),16000)
        url=urlsplit(stream._signed_url())
        query=dict(parse_qsl(url.query)); actual=query.pop('signature')
        canonical=url.netloc+url.path+'?'+'&'.join(k+'='+query[k] for k in sorted(query))
        expected=base64.b64encode(hmac.new(b'test-secret-not-real',canonical.encode(),hashlib.sha1).digest()).decode()
        self.assertEqual(actual,expected)

    def test_native_open_translation_is_not_a_final(self):
        bus=NativeTranslationBus()
        bus.record(item_id='a',source_text='I do not agree.',translation='I',anchors=(('I do not agree.','I'),))
        self.assertIsNone(bus._take('a','I do not agree.'))
        bus.close_item('a',source_text='I do not agree.',translation='Ich stimme nicht zu.')
        self.assertEqual(bus._take('a','I do not agree.'),'Ich stimme nicht zu.')

    def test_native_closed_segment_ignores_earlier_partial_anchor(self):
        bus=NativeTranslationBus()
        bus.close_item('a',source_text='I do not agree.',translation='Ich stimme nicht zu.',anchors=(('I do not agree.','I'),))
        self.assertEqual(bus._take('a','I do not agree.'),'Ich stimme nicht zu.')

class ReadinessTests(unittest.TestCase):
    def test_blocked_modes_fail_before_start(self):
        from companion.providers.readiness import validate_asr_start
        from companion.providers.base import ProviderRequestError
        for cfg in ({'kind':'openai-realtime-transcription','model':'gpt-live-transcribe'},
                    {'kind':'elevenlabs-scribe-realtime','options':{'commitStrategy':'manual'}}):
            with self.assertRaises(ProviderRequestError): validate_asr_start(cfg)

    def test_saved_summary_never_contains_credentials_or_endpoint(self):
        from companion.providers.effective import effective_settings
        import json
        cfg=copy.deepcopy(DEFAULT_CONFIG)
        for group in ('asr','translation'):
            cfg[group]['providers'][0].update(apiKey='SENTINEL_SECRET',_apiKey='SENTINEL_SECRET',baseUrl='SENTINEL_URL')
        text=json.dumps(effective_settings(cfg,'test/providers.json'))
        self.assertNotIn('SENTINEL',text)
        self.assertIn('saved-next-start',text)

    def test_stale_recommendation_cannot_change_readiness(self):
        from companion.providers.readiness import asr_readiness
        cfg={'kind':'soniox-realtime','model':'stt-rt-v5','recommended':True,'options':{'translationType':'one_way'}}
        self.assertEqual(asr_readiness(cfg)['tier'],'experimental')

if __name__ == '__main__':
    unittest.main()
