from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.languages import (
    LanguageNotSupportedError,
    autonym,
    canonicalize_tag,
    canonicalize_tag_or_none,
    canonicalize_target_tag,
    catalog_entries,
    direction_for,
    display_name,
    language_catalog,
    primary_subtag,
)
from companion.providers.base import (
    ASRLanguageCapabilities,
    SourceLanguagePolicy,
    TranslationLanguageCapabilities,
    validate_source_policy,
    validate_translation_pair,
)


class CanonicalizationTests(unittest.TestCase):
    def test_standardizes_case_and_underscores(self) -> None:
        self.assertEqual(canonicalize_tag("eng_US"), "en-US")
        self.assertEqual(canonicalize_tag("JA"), "ja")
        self.assertEqual(canonicalize_tag("pt-br"), "pt-BR")
        self.assertEqual(canonicalize_tag("sr-Latn"), "sr-Latn")

    def test_legacy_codes_are_replaced(self) -> None:
        self.assertEqual(canonicalize_tag("iw"), "he")
        self.assertEqual(canonicalize_tag("iw-IL"), "he-IL")

    def test_zh_alias_policy_collapses_regions_onto_scripts(self) -> None:
        self.assertEqual(canonicalize_tag("zh-CN"), "zh-Hans")
        self.assertEqual(canonicalize_tag("zh-SG"), "zh-Hans")
        self.assertEqual(canonicalize_tag("zh-TW"), "zh-Hant")
        self.assertEqual(canonicalize_tag("zh-HK"), "zh-Hant")
        self.assertEqual(canonicalize_tag("zh-MO"), "zh-Hant")
        self.assertEqual(canonicalize_tag("zh-Hant"), "zh-Hant")
        self.assertEqual(canonicalize_tag("zh-Hans"), "zh-Hans")

    def test_script_and_region_distinctions_are_preserved(self) -> None:
        self.assertNotEqual(canonicalize_tag("pt-BR"), canonicalize_tag("pt-PT"))
        self.assertNotEqual(canonicalize_tag("sr-Cyrl"), canonicalize_tag("sr-Latn"))
        self.assertEqual(canonicalize_tag("pt-BR"), "pt-BR")
        self.assertEqual(canonicalize_tag("pt-PT"), "pt-PT")

    def test_rejects_invalid_tags_and_auto(self) -> None:
        for bad in ("", "auto", "AUTO", "xx-invalid-12345", "123", "und"):
            with self.assertRaises(ValueError, msg=bad):
                canonicalize_tag(bad)
        self.assertIsNone(canonicalize_tag_or_none("auto"))
        self.assertIsNone(canonicalize_tag_or_none(None))
        self.assertEqual(canonicalize_tag_or_none("zh-TW"), "zh-Hant")

    def test_target_tag_maps_legacy_zh_to_zh_hans(self) -> None:
        self.assertEqual(canonicalize_target_tag("zh"), "zh-Hans")
        self.assertEqual(canonicalize_target_tag("ja"), "ja")
        self.assertEqual(canonicalize_target_tag("zh-TW"), "zh-Hant")

    def test_primary_subtag_for_provider_private_codes(self) -> None:
        self.assertEqual(primary_subtag("zh-Hans"), "zh")
        self.assertEqual(primary_subtag("pt-BR"), "pt")
        self.assertEqual(primary_subtag("ja"), "ja")


class CatalogTests(unittest.TestCase):
    def test_catalog_is_checked_in_compact_identity_data(self) -> None:
        catalog = language_catalog()
        self.assertEqual(catalog["version"], 1)
        entries = catalog_entries()
        self.assertGreaterEqual(len(entries), 80)
        tags = [entry["tag"] for entry in entries]
        self.assertEqual(tags, sorted(tags))
        self.assertEqual(len(tags), len(set(tags)))
        for entry in entries:
            self.assertIn("englishName", entry)
            self.assertIn("autonym", entry)
            self.assertIn(entry["direction"], ("ltr", "rtl"))
            for alias in entry["aliases"]:
                self.assertEqual(canonicalize_tag(alias), entry["tag"])

    def test_script_and_region_entries_carry_names_autonyms_and_direction(self) -> None:
        by_tag = {entry["tag"]: entry for entry in catalog_entries()}
        self.assertEqual(by_tag["zh-Hans"]["englishName"], "Chinese (Simplified)")
        self.assertEqual(by_tag["zh-Hant"]["englishName"], "Chinese (Traditional)")
        self.assertNotEqual(by_tag["zh-Hans"]["autonym"], by_tag["zh-Hant"]["autonym"])
        self.assertEqual(by_tag["pt-BR"]["englishName"], "Portuguese (Brazil)")
        self.assertEqual(by_tag["pt-PT"]["englishName"], "Portuguese (Portugal)")
        self.assertEqual(by_tag["ar"]["direction"], "rtl")
        self.assertEqual(by_tag["he"]["direction"], "rtl")
        self.assertEqual(by_tag["ja"]["direction"], "ltr")

    def test_display_helpers_fall_back_to_tag_and_direction_heuristics(self) -> None:
        self.assertEqual(display_name("ja"), "Japanese")
        self.assertEqual(autonym("zh-Hant"), "中文（繁體）")
        self.assertEqual(display_name("tlh"), "tlh")  # not in the catalog
        self.assertEqual(direction_for("ar"), "rtl")
        self.assertEqual(direction_for("he-IL"), "rtl")
        self.assertEqual(direction_for("en"), "ltr")


class SourceLanguagePolicyTests(unittest.TestCase):
    def test_specified_policy_round_trips_canonical_json(self) -> None:
        policy = SourceLanguagePolicy.from_json({"mode": "specified", "tag": "zh-TW"})
        self.assertEqual(policy.mode, "specified")
        self.assertEqual(policy.tag, "zh-Hant")
        self.assertEqual(policy.to_json(), {"mode": "specified", "tag": "zh-Hant"})
        self.assertEqual(policy.fallback_language, "zh-Hant")

    def test_legacy_plain_string_becomes_specified(self) -> None:
        policy = SourceLanguagePolicy.from_json("ja")
        self.assertEqual(policy.to_json(), {"mode": "specified", "tag": "ja"})

    def test_detect_policy_validates_candidates_preferred_and_duplicates(self) -> None:
        policy = SourceLanguagePolicy.from_json({
            "mode": "detect",
            "candidates": ["ja", "en", "zh-CN", "en"],
            "preferred": "zh-Hans",
            "allowCodeSwitching": True,
        })
        self.assertEqual(policy.candidates, ("ja", "en", "zh-Hans"))
        self.assertEqual(policy.preferred, "zh-Hans")
        self.assertTrue(policy.allow_code_switching)
        self.assertEqual(policy.fallback_language, "zh-Hans")
        self.assertEqual(
            policy.to_json(),
            {
                "mode": "detect",
                "candidates": ["ja", "en", "zh-Hans"],
                "allowCodeSwitching": True,
                "preferred": "zh-Hans",
            },
        )

    def test_invalid_policies_raise(self) -> None:
        with self.assertRaises(ValueError):
            SourceLanguagePolicy.from_json({"mode": "specified"})
        with self.assertRaises(ValueError):
            SourceLanguagePolicy.from_json({"mode": "specified", "tag": "ja", "candidates": ["en"]})
        with self.assertRaises(ValueError):
            SourceLanguagePolicy.from_json({"mode": "detect", "candidates": ["ja"], "preferred": "en"})
        with self.assertRaises(ValueError):
            SourceLanguagePolicy.from_json({"mode": "detect", "candidates": ["auto"]})
        with self.assertRaises(ValueError):
            SourceLanguagePolicy.from_json({"mode": "guessing"})
        with self.assertRaises(ValueError):
            SourceLanguagePolicy.from_json(42)


class CapabilityValidationTests(unittest.TestCase):
    def test_specified_tag_must_be_in_supported_list(self) -> None:
        capabilities = ASRLanguageCapabilities(supported_tags=("zh", "en", "ja"), detection="none")
        validate_source_policy(SourceLanguagePolicy.specified("ja"), capabilities)
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(SourceLanguagePolicy.specified("fr"), capabilities)

    def test_unqualified_supported_tag_covers_more_specific_policy_tags(self) -> None:
        # BCP 47 basic-range semantics: "zh" covers zh-Hans, but zh-Hans does
        # not cover zh-Hant and a specific tag does not cover the bare one.
        capabilities = ASRLanguageCapabilities(supported_tags=("zh", "en", "ja"), detection="none")
        validate_source_policy(SourceLanguagePolicy.specified("zh-Hans"), capabilities)
        validate_source_policy(SourceLanguagePolicy.specified("zh-Hant"), capabilities)
        specific = ASRLanguageCapabilities(supported_tags=("zh-Hans",), detection="none")
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(SourceLanguagePolicy.specified("zh-Hant"), specific)
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(SourceLanguagePolicy.specified("zh"), specific)

    def test_detection_modes_are_enforced(self) -> None:
        no_detection = ASRLanguageCapabilities(supported_tags=("ja",), detection="none")
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(SourceLanguagePolicy.detect(), no_detection)

        candidates_only = ASRLanguageCapabilities(
            supported_tags=None, detection="candidates", max_candidates=4
        )
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(SourceLanguagePolicy.detect(), candidates_only)
        validate_source_policy(SourceLanguagePolicy.detect(candidates=("ja", "en")), candidates_only)
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(
                SourceLanguagePolicy.detect(candidates=("ja", "en", "ko", "fr", "de")),
                candidates_only,
            )

        unrestricted = ASRLanguageCapabilities(supported_tags=None, detection="unrestricted")
        validate_source_policy(SourceLanguagePolicy.detect(), unrestricted)

    def test_code_switching_requires_declared_support(self) -> None:
        capabilities = ASRLanguageCapabilities(supported_tags=None, detection="unrestricted", code_switching=False)
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(SourceLanguagePolicy.detect(allow_code_switching=True), capabilities)
        switching = ASRLanguageCapabilities(supported_tags=None, detection="unrestricted", code_switching=True)
        validate_source_policy(SourceLanguagePolicy.detect(allow_code_switching=True), switching)

    def test_unknown_experimental_model_cannot_claim_detection(self) -> None:
        experimental = ASRLanguageCapabilities()  # defaults: experimental, no detection
        self.assertEqual(experimental.tier, "experimental")
        self.assertFalse(experimental.code_switching)
        with self.assertRaises(LanguageNotSupportedError):
            validate_source_policy(SourceLanguagePolicy.detect(), experimental)

    def test_translation_pair_validation(self) -> None:
        open_world = TranslationLanguageCapabilities(open_world_prompting=True, tier="provider_claimed")
        validate_translation_pair("ja", "sw", open_world)

        closed = TranslationLanguageCapabilities(
            source_tags=("ja", "en"), target_tags=("zh-Hans", "en"), open_world_prompting=False
        )
        validate_translation_pair("ja", "zh-Hans", closed)
        with self.assertRaises(LanguageNotSupportedError):
            validate_translation_pair("ja", "sw", closed)
        with self.assertRaises(LanguageNotSupportedError):
            validate_translation_pair("fr", "zh-Hans", closed)
        # An unresolved detected source cannot be pair-checked pre-start.
        validate_translation_pair(None, "zh-Hans", closed)
        with self.assertRaises(LanguageNotSupportedError):
            validate_translation_pair(None, "sw", closed)


if __name__ == "__main__":
    unittest.main()
