"""Canonical BCP 47 language identity for LingerLens.

Rules (see CONTEXT.md and the global-language MVP spec):

* Every language LingerLens stores or passes around is a canonical BCP 47 tag
  (``ja``, ``zh-Hant``, ``pt-BR``). ``auto`` is never a tag; it is expressed
  as a ``SourceLanguagePolicy`` mode instead.
* Server-side validation is authoritative and uses ``langcodes``. The
  runtime does NOT require ``language_data``: display names, autonyms and
  directions come from the checked-in generated catalog
  ``companion/data/languages.json``.
* The catalog is language identity data, not a support claim. Whether a
  Provider profile supports a tag comes from its language capabilities.

LingerLens alias policy (on top of ``langcodes.standardize_tag``):

* ``zh-CN``/``zh-SG`` collapse to ``zh-Hans``; ``zh-TW``/``zh-HK``/``zh-MO``
  collapse to ``zh-Hant``. Script, not region, is the identity that matters
  for subtitles, and the product must never conflate Simplified/Traditional.
* A target language of bare ``zh`` means the legacy Simplified-Chinese
  behaviour and canonicalizes to ``zh-Hans`` (``canonicalize_target_tag``).
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import langcodes
from langcodes import Language

CATALOG_PATH = Path(__file__).resolve().parent / "data" / "languages.json"

# zh region tags collapse onto the script-only identity (product alias policy).
_ZH_REGION_TO_SCRIPT = {"CN": "Hans", "SG": "Hans", "TW": "Hant", "HK": "Hant", "MO": "Hant"}

# Direction fallback for tags outside the catalog: the primary subtag decides.
_RTL_PRIMARY = {"ar", "he", "fa", "ur", "ps", "sd", "ug", "dv", "ks", "yi", "ckb", "ku"}
_RTL_SCRIPTS = {"Arab", "Hebr", "Syrc", "Thaa", "Nkoo", "Adlm", "Rohg"}


class LanguageNotSupportedError(ValueError):
    """A source policy or language pair the active Provider cannot honor.

    Raised before playback starts so a Provider never silently transcribes or
    translates in the wrong language (spec user story 5).
    """


@functools.lru_cache(maxsize=1)
def language_catalog() -> dict[str, Any]:
    """Load the checked-in generated catalog once per process."""
    with CATALOG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@functools.lru_cache(maxsize=1)
def _catalog_by_tag() -> dict[str, dict[str, Any]]:
    return {entry["tag"]: entry for entry in language_catalog()["languages"]}


@functools.lru_cache(maxsize=1)
def _alias_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for entry in language_catalog()["languages"]:
        for alias in entry.get("aliases", []):
            aliases[alias] = entry["tag"]
    return aliases


def catalog_entries() -> list[dict[str, Any]]:
    return list(language_catalog()["languages"])


def canonicalize_tag(raw: object) -> str:
    """Normalize one tag to LingerLens's canonical BCP 47 form or raise."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("language tag is required")
    if text.lower() == "auto":
        raise ValueError('"auto" is a detection policy, not a language tag')
    try:
        standardized = langcodes.standardize_tag(text)
    except Exception as exc:
        raise ValueError(f"invalid language tag: {text!r}") from exc
    if not langcodes.tag_is_valid(standardized):
        raise ValueError(f"invalid language tag: {text!r}")
    language = Language.get(standardized)
    if not language.language or language.language == "und":
        raise ValueError(f"invalid language tag: {text!r}")
    # Catalog aliases collapse first (zh-CN -> zh-Hans, iw -> he).
    collapsed = _alias_map().get(standardized)
    if collapsed is not None:
        return collapsed
    # Region-tagged zh outside the catalog aliases still collapses by script.
    region = _region(language)
    if language.language == "zh" and not language.script and region in _ZH_REGION_TO_SCRIPT:
        return f"zh-{_ZH_REGION_TO_SCRIPT[region]}"
    return standardized


def canonicalize_tag_or_none(raw: object) -> str | None:
    """Adapter-edge normalization: provider-reported languages never raise."""
    try:
        return canonicalize_tag(raw)
    except (ValueError, TypeError):
        return None


def canonicalize_target_tag(raw: object) -> str:
    """Canonicalize a target language; legacy bare ``zh`` means zh-Hans."""
    text = str(raw or "").strip()
    if text == "zh":
        return "zh-Hans"
    return canonicalize_tag(text)


def primary_subtag(tag: str) -> str:
    """The ISO 639 primary subtag, for Provider-private code mapping."""
    return Language.get(tag).language or tag


def _region(language: Language) -> str | None:
    # langcodes 3.5 renamed ``region`` to ``territory``; avoid the deprecated
    # alias (it warns) while still supporting older 3.x releases.
    if hasattr(language, "territory"):
        return language.territory
    return language.region


def display_name(tag: str) -> str:
    """Stable English display name from the catalog; the tag itself if unknown."""
    entry = _catalog_by_tag().get(tag)
    return str(entry["englishName"]) if entry else tag


@functools.lru_cache(maxsize=1)
def _primary_names() -> dict[str, str]:
    """English names of the catalog's primary-language entries (``ja``, ``fr`` ...)."""
    return {
        str(entry["tag"]): str(entry["englishName"])
        for entry in catalog_entries()
        if entry["tag"] == entry.get("language")
    }


def primary_language_name(tag: str) -> str | None:
    """English name of the tag's primary language from the catalog, or None.

    Only catalog entries whose tag is itself a primary language contribute
    (``ja`` -> ``Japanese``); zh/pt have no primary entry, so callers with a
    provider-specific vocabulary keep their own tiny overrides.
    """
    return _primary_names().get(primary_subtag(tag))


def autonym(tag: str) -> str:
    entry = _catalog_by_tag().get(tag)
    return str(entry["autonym"]) if entry else tag


def direction_for(tag: str) -> str:
    """``ltr``/``rtl``: catalog direction, with a script/primary fallback."""
    entry = _catalog_by_tag().get(tag)
    if entry:
        return str(entry.get("direction") or "ltr")
    language = Language.get(tag)
    if language.script in _RTL_SCRIPTS or language.language in _RTL_PRIMARY:
        return "rtl"
    return "ltr"
