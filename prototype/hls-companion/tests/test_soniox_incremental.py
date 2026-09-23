import unittest
from types import SimpleNamespace
from unittest.mock import patch

from companion.providers import asr_soniox_realtime as soniox
from companion.providers.base import SourceLanguagePolicy


def stream():
    return soniox._SonioxStream(SimpleNamespace(options={}), SourceLanguagePolicy.specified('ja'), 16000, [])


class IncrementalSonioxTests(unittest.TestCase):
    def test_long_segment_does_not_retokenize_all_confirmed_history(self):
        subject = stream()
        work = 0
        tokenize = soniox._lexical_tokens
        def counted(tokens, stable):
            nonlocal work
            work += len(tokens)
            return tokenize(tokens, stable)
        emitted = []
        with patch.object(soniox, '_lexical_tokens', counted):
            for index in range(1000):
                events = subject._map_event({'tokens': [{'text': 'あ', 'language': 'ja', 'is_final': True,
                                                        'start_ms': index * 100, 'end_ms': index * 100 + 90}]})
                emitted.extend(token.text for event in events if event.caption_observation for token in event.caption_observation.tokens)
            for _ in range(20):
                subject._map_event({'tokens': []})
        self.assertEqual(''.join(emitted), 'あ' * 1000)
        self.assertLessEqual(work, 3000, 'unchanged history must not be tokenized for every update')

    def test_words_languages_duplicates_and_endpoints_preserve_text_and_timing(self):
        subject = stream()
        tokens = [{'text': text, 'language': language, 'is_final': True, 'start_ms': i * 100,
                   'end_ms': i * 100 + 90, 'speaker': str(i % 2), 'confidence': .9}
                  for i, (text, language) in enumerate([('Hel', 'en'), ('lo', 'en'), (' world', 'en'),
                                                       ('。', 'ja'), ('今日', 'ja'), ('は', 'ja'), (' test', 'en')])]
        emitted = []
        for token in tokens:
            events = subject._map_event({'tokens': [token]})
            emitted.extend(t for event in events if event.caption_observation for t in event.caption_observation.tokens)
            duplicate = subject._map_event({'tokens': [token]})
            self.assertFalse(any(event.caption_observation.tokens for event in duplicate if event.caption_observation))
        final = subject._map_event({'tokens': [{'text': '<end>', 'is_final': True}]})
        emitted.extend(t for event in final if event.caption_observation for t in event.caption_observation.tokens)
        self.assertEqual(emitted, soniox._lexical_tokens(tokens, True))
        finished = next(event for event in final if event.type == 'final')
        self.assertEqual(finished.text, ''.join(token['text'] for token in tokens))
        self.assertEqual((finished.begin_pcm, finished.end_pcm), (0, .69))
        next_events = subject._map_event({'tokens': [{'text': '新', 'language': 'ja', 'is_final': True, 'start_ms': 800, 'end_ms': 900}]})
        self.assertEqual(next_events[0].text, '新')


if __name__ == '__main__':
    unittest.main()
