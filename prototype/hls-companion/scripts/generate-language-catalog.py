#!/usr/bin/env python3
"""Generate companion/data/languages.json (the checked-in compact catalog).

Development-only script: it needs ``language_data`` (CLDR names), which the
runtime deliberately does NOT require. The runtime ships with ``langcodes``
only and reads this generated file. Regenerate with:

    pip install langcodes "language_data>=1.3,<2"
    python scripts/generate-language-catalog.py

The catalog is language identity data (canonical tag, display names,
direction, aliases). It is NOT a claim that every Provider supports every
entry; Provider support tiers come from model presets.

Fixed inputs:
  * ``SEED_TAGS`` -- the languages LingerLens exposes in the selector.
  * ``ALIASES`` -- LingerLens's alias policy: region/legacy tags that collapse
    onto a canonical catalog entry (e.g. zh-CN -> zh-Hans, iw -> he).
"""
from __future__ import annotations

import json
import sys
from importlib.metadata import version
from pathlib import Path

import langcodes
from langcodes import Language

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "companion" / "data" / "languages.json"

# Broad but compact: the languages a global live viewer is realistically
# likely to caption or translate into, including script/region distinctions
# the MVP explicitly cares about (zh-Hans/zh-Hant, pt-BR/pt-PT, sr-Latn).
SEED_TAGS: tuple[str, ...] = (
    "af", "am", "ar", "az", "bg", "bn", "bs", "ca", "ceb", "ckb", "cs", "cy",
    "da", "de", "dv", "el", "en", "eo", "es", "es-419", "et", "eu", "fa",
    "fi", "fr", "fy", "ga", "gl", "gu", "ha", "haw", "he", "hi", "hr", "hu",
    "hy", "id", "ig", "is", "it", "ja", "jv", "ka", "kk", "km", "kn", "ko",
    "ku", "la", "lb", "lo", "lt", "lv", "mi", "mk", "ml", "mn", "mr", "ms",
    "mt", "my", "nb", "ne", "nl", "pa", "pl", "ps", "pt-BR", "pt-PT", "ro",
    "ru", "sd", "si", "sk", "sl", "so", "sq", "sr-Cyrl", "sr-Latn", "su",
    "sv", "sw", "ta", "te", "th", "tl", "tr", "ug", "uk", "ur", "uz", "vi",
    "xh", "yi", "yo", "yue", "zh-Hans", "zh-Hant", "zu",
)

# Alias policy: tags users may type that resolve to a catalog entry.
ALIASES: dict[str, str] = {
    "zh-CN": "zh-Hans", "zh-SG": "zh-Hans",
    "zh-TW": "zh-Hant", "zh-HK": "zh-Hant", "zh-MO": "zh-Hant",
    "no": "nb", "iw": "he", "in": "id", "ji": "yi",
    "pt": "pt-PT", "sr": "sr-Cyrl",
}

RTL_SCRIPTS = {"Arab", "Hebr", "Syrc", "Thaa", "Nkoo", "Adlm", "Rohg"}
RTL_PRIMARY = {"ar", "he", "fa", "ur", "ps", "sd", "ug", "dv", "ks", "yi", "ckb", "ku"}


def _region(language: Language) -> str | None:
    if hasattr(language, "territory"):
        return language.territory
    return language.region


def english_name(language: Language) -> str:
    base = language.language_name()
    region = _region(language)
    if language.script:
        return f"{base} ({language.script_name()})"
    if region:
        territory_name = getattr(language, "territory_name", None) or language.region_name
        return f"{base} ({territory_name()})"
    return base


def direction(language: Language) -> str:
    if language.script in RTL_SCRIPTS:
        return "rtl"
    if language.language in RTL_PRIMARY:
        return "rtl"
    return "ltr"


def main() -> int:
    try:
        cldr_version = version("language_data")
    except Exception:
        print("language_data is required to generate the catalog", file=sys.stderr)
        return 1
    aliases_by_tag: dict[str, list[str]] = {}
    for alias, canonical in ALIASES.items():
        if canonical not in SEED_TAGS:
            raise SystemExit(f"alias {alias!r} points at unknown tag {canonical!r}")
        aliases_by_tag.setdefault(canonical, []).append(alias)

    entries = []
    for tag in SEED_TAGS:
        language = Language.get(tag)
        if not language.is_valid():
            raise SystemExit(f"seed tag {tag!r} is not a valid BCP 47 tag")
        entries.append({
            "tag": tag,
            "englishName": english_name(language),
            "autonym": language.autonym(),
            "language": language.language,
            "script": language.script,
            "region": _region(language),
            "direction": direction(language),
            "aliases": sorted(aliases_by_tag.get(tag, [])),
        })
    entries.sort(key=lambda entry: entry["tag"])

    payload = {
        "version": 1,
        "cldrVersion": cldr_version,
        "langcodesVersion": version("langcodes"),
        "languages": entries,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {OUTPUT} ({len(entries)} languages, cldr {cldr_version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
