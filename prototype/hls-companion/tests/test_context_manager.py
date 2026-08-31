from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("laglingo_context_manager", ROOT / "companion" / "context_manager.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class RollingContextTests(unittest.TestCase):
    def test_trims_by_age_and_pair_count_oldest_first(self) -> None:
        context = MODULE.RollingContext(context_pairs=3, context_seconds=90)
        for index, timestamp in enumerate((1, 20, 40, 80, 100)):
            context.add(f"s{index}", f"z{index}", timestamp)
        self.assertEqual(context.history(100), [("s2", "z2"), ("s3", "z3"), ("s4", "z4")])
        self.assertEqual(context.history(100, pair_limit=2), [("s3", "z3"), ("s4", "z4")])
        context.trim(100)
        self.assertEqual(context.history(100, pair_limit=10), [("s2", "z2"), ("s3", "z3"), ("s4", "z4")])

    def test_exact_prompt_structure_and_glossary_limit(self) -> None:
        meta = MODULE.StreamMeta("船长直播", "Marine", "游戏", "ja", "zh")
        glossary = [(f"term{i}", f"target{i}") for i in range(31)]
        system, user = MODULE.build_prompt(
            "今から始めます",
            meta,
            [("前の文", "上一句"), ("次の文", "下一句")],
            glossary,
        )
        expected_prefix = (
            "你是直播字幕翻译器。把 CURRENT 从日语译成中文。\n"
            "规则：\n"
            "1. 只输出 CURRENT 的译文，不要输出解释、不要重复 HISTORY。\n"
            "2. HISTORY 只用于理解指代、省略主语和话题，不要翻译它。\n"
            "3. 译文要像直播字幕：简洁、口语、可一眼读完。\n"
            "4. 不要补全说话人没说完的内容，不要添加未表达的事实。\n"
            "5. 人名/专有名词严格遵循术语表。\n"
            "6. 只输出译文本身，不加引号、不加前缀。\n"
            "直播信息：船长直播 / Marine / 领域：游戏\n"
            "术语表：\n"
        )
        self.assertTrue(system.startswith(expected_prefix))
        self.assertIn("term29 => target29", system)
        self.assertNotIn("term30 => target30", system)
        self.assertEqual(user, "HISTORY:\n前の文 -> 上一句\n次の文 -> 下一句\nCURRENT:\n今から始めます")

    def test_empty_history_keeps_exact_history_and_current_blocks(self) -> None:
        self.assertEqual(MODULE.build_user_prompt("現在", []), "HISTORY:\n\nCURRENT:\n現在")


if __name__ == "__main__":
    unittest.main()
