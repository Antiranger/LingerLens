from __future__ import annotations
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from companion.punctuation_boundaries import select_boundaries


def select(parts, times, lang='en', endpoints=()):
    edges=[]; total=0
    for p in parts: total+=len(p); edges.append(total)
    return select_boundaries(''.join(parts),edges,[0]+times[:-1],times,lang,{edges[i] for i in endpoints})


class PunctuationBoundaryTests(unittest.TestCase):
    def test_four_seconds_is_audio_span_not_word_count(self):
        self.assertEqual(select(['We started,'],[3.99]),[])
        self.assertEqual(select(['We started,'],[4]),[(0,'clause_boundary')])

    def test_no_predicate_dictionary_required_across_languages(self):
        for lang,text in [('en','The conference participants, finally ready,'),('es','Los participantes de la conferencia, preparados,'),('pt-BR','Os participantes da conferência, preparados,'),('zh-Hans','今天的事情先讲到这里，'),('ko','오늘 이야기는 여기까지,')]:
            with self.subTest(lang=lang):self.assertEqual(select([text],[5],lang),[(0,'clause_boundary')])

    def test_short_terminal_is_immediate(self):
        self.assertEqual(select(['Done.'],[.3]),[(0,'terminal_punctuation')])
        self.assertEqual(select(['終わりました。'],[.3],'ja'),[(0,'terminal_punctuation')])

    def test_numeric_sequence_with_space_is_protected(self):
        out=select(['You send 3,',' 4 guys in middle,'],[5,7])
        self.assertEqual(out,[(1,'clause_boundary')])
        self.assertEqual(select(['Value 3.','14 today.'],[5,7]),[(1,'terminal_punctuation')])

    def test_number_sentence_end_at_endpoint_is_valid(self):
        self.assertEqual(select(['We finished round 19.'],[3],endpoints=(0,)),[(0,'terminal_punctuation')])

    def test_abbreviation_and_title_before_name(self):
        self.assertEqual(select(['U.S.',' policy changed.'],[4,6]),[(1,'terminal_punctuation')])
        self.assertEqual(select(['Ask Dr.',' Smith about it.'],[4,6]),[(1,'terminal_punctuation')])
        self.assertEqual(select(['https://example.com.'],[4]),[])

    def test_no_invented_internal_time_and_no_forced_duration_cut(self):
        self.assertEqual(select(['A long phrase without punctuation'],[30]),[])
        self.assertEqual(select(['We finished, and we started again.'],[15]),[(0,'terminal_punctuation')])

    def test_genitive_protected_even_when_right_has_not_arrived(self):
        self.assertEqual(select(['学校一の美人の、'],[5],'ja'),[])
        self.assertEqual(select(['昨日の、'],[5],'ja'),[])

    def test_fillers_do_not_discharge_genitive_protection(self):
        for filler in ['なんか、','えー、']:
            with self.subTest(filler=filler):
                self.assertEqual(select(['学校一の美人の、',filler,'女の子がいて、'],[5,6,9],'ja'),[(2,'clause_boundary')])

    def test_colloquial_no_and_question_are_not_genitive(self):
        self.assertEqual(select(['そのなんて言うの、'],[5],'ja'),[(0,'clause_boundary')])
        self.assertEqual(select(['もう食べないの？'],[2],'ja'),[(0,'terminal_punctuation')])

    def test_visible_attached_particle(self):
        for left,right in [('検索して、','も見つからない。'),('食べないの？','が不思議です。')]:
            with self.subTest(left=left):self.assertEqual(select([left,right],[5,7],'ja'),[(1,'terminal_punctuation')])

    def test_discourse_starters_do_not_block_previous_sentence(self):
        for left,right in [('それ。','だって、話が違う。'),('なるほどな。','でもそうやな。'),('終わりました。','でも問題があります。')]:
            with self.subTest(left=left):self.assertEqual(select([left,right],[2,4],'ja'),[(0,'terminal_punctuation'),(1,'terminal_punctuation')])

    def test_adverbial_with_visible_following_clause(self):
        self.assertEqual(select(['海に流れてすごく、','海も抹茶色になっていた。'],[5,8],'ja'),[(1,'terminal_punctuation')])

    def test_visible_tiny_tail_merges_but_future_tail_is_not_awaited(self):
        self.assertEqual(select(['We have finished this part,'],[5]),[(0,'clause_boundary')])
        self.assertEqual(select(['We have finished this part,',' right?'],[5,5.5]),[(1,'terminal_punctuation')])
        self.assertEqual(select(['Done.',' Yes.'],[1,1.5]),[(0,'terminal_punctuation'),(1,'terminal_punctuation')])

    def test_separately_arrived_closer_attaches(self):
        self.assertEqual(select(['He said "Done.', '"'],[4,4]),[(1,'terminal_punctuation')])

if __name__=='__main__':unittest.main()
