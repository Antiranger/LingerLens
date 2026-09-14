# LagLingo Language Domain & Internationalization Architecture
**Document Reference:** `docs/research/language-i18n-architecture.md`  
**Status:** Preliminary research proposal; final implementation plan is authoritative  
**Scope:** Companion Server (Python 3.11+), Web Player (Vanilla JS / Browser), Provider Subsystems (ASR & MT)

---

## 1. Executive Summary & Current State Audit

LagLingo is a local streaming companion that captures live HLS/DASH media streams, performs real-time Automated Speech Recognition (ASR), and translates utterances into live subtitles (Machine Translation, MT) with synchronized playback delay.

### 1.1 Current Codebase Bottlenecks
A comprehensive audit of the LagLingo prototype identifies several tightly-coupled language assumptions:
1. **Hardcoded Language Defaults**:
   - `prototype/hls-companion/companion/providers/config.py`: Hardcoded `subtitle.sourceLanguage = "ja"` and `subtitle.targetLanguage = "zh"`.
   - `prototype/hls-companion/companion/server.py`: Fallback extraction in `_prepare_subtitles` defaults to `"ja"` and `"zh"` without tag validation.
2. **Provider-Specific Coupling**:
   - `providers/mt_qwen_mt.py`: Directly forces `source_lang` and `target_lang` into DashScope Qwen-MT parameters (`source_language`, `target_language`).
   - `providers/mt_openai_compat.py`: Hardcodes English prompt templates: `"Translate the following {source_lang} subtitles to {target_lang}..."` without canonical display names or language normalization.
   - `providers/asr_qwen_realtime.py` & `providers/asr_dashscope_task.py`: Capabilities tuples store arbitrary strings (`"zh"`, `"yue"`, `"en"`, `"ja"`, etc.) without canonical normalization or dialect negotiation.
3. **UI / Frontend Limitations**:
   - `web-player/player.js` & `web-player/index.html`: Model settings only expose language options as free-text input fields (e.g. `<input data-option="language" value="ja">`), lacking validation, localized display names, search capabilities, or script/region variant handling.
   - Subtitle rendering has no bidirectional / Right-To-Left (RTL) text support or CSS directionality awareness (`dir="auto"`).

---

## 2. Standards Baseline: BCP 47 & Unicode CLDR

To achieve rock-solid interoperability across diverse ASR engines, Machine Translation APIs, Large Language Models (LLMs), and frontend display components, LagLingo must adopt formal internationalization specifications.

```
+-----------------------------------------------------------------------------------+
|                            IETF BCP 47 Language Tag                               |
|                                                                                   |
|    zh          -       Hans        -         CN         -         u-co-pinyin     |
|  [Language]         [Script]             [Region]              [Extensions]       |
|  (ISO 639-1/2/3)   (ISO 15924)          (ISO 3166-1/            (BCP 47 / UTS 35) |
|   2-3 letters       4 letters             UN M.49)                                |
|                                         2 letters/3 digits                        |
+-----------------------------------------------------------------------------------+
```

### 2.1 Canonical Identifier Standard (IETF BCP 47 / RFC 5646)
- **Structure**: `language[-extlang][-script][-region][-variant][-extension][-privateuse]`.
- **Case Convention**: Lowercase language (`zh`), titlecase script (`Hans`), uppercase region (`CN` or `TW`). Tag comparisons must be ASCII case-insensitive.
- **Canonicalization**:
  1. Deprecated subtag substitution (e.g., `iw` $\to$ `he`, `in` $\to$ `id`, `mo` $\to$ `ro`).
  2. Redundant tag replacement (e.g., `zh-cmn-Hans` $\to$ `zh-Hans`).
  3. Suppressed script removal: According to BCP 47 / CLDR, default scripts are suppressed when they represent the canonical primary writing system of an unregionalized language (e.g. `en-Latn` $\to$ `en`, `ja-Jpan` $\to$ `ja`), but retained when distinguishing dialects or orthogonal scripts (e.g., `zh-Hans` vs `zh-Hant`, `sr-Cyrl` vs `sr-Latn`).

### 2.2 Unicode CLDR (UTS #35) & Likely Subtags
The Unicode Common Locale Data Repository (CLDR) provides canonical XML/JSON data defining:
- **Likely Subtags**: Resolves ambiguous or minimal tags into maximal tags, and vice versa:
  - `zh` $\to$ `zh-Hans-CN`
  - `zh-TW` $\to$ `zh-Hant-TW`
  - `pt` $\to$ `pt-Latn-BR` (or `pt-PT` depending on locale policy)
- **Display Name Trees**: Localized names for language codes across hundreds of world languages.

---

## 3. Core Linguistic Challenges & Variants

### 3.1 Chinese: Scripts, Regions, and Macrolanguages
- In ISO 639, `zh` is a macrolanguage. In modern digital practice:
  - **Simplified Chinese**: `zh-Hans` (Primary region: `CN`, `SG`).
  - **Traditional Chinese**: `zh-Hant` (Primary regions: `TW`, `HK`, `MO`).
  - *Legacy codes*: `zh-CN` should map to `zh-Hans`, and `zh-TW` / `zh-HK` should map to `zh-Hant`.
  - *Cantonese*: Represented as `yue` (ISO 639-3) or `zh-yue`. For ASR and MT engines (e.g. Qwen, Whisper), `yue` is the canonical identifier.

### 3.2 Portuguese: European vs. Brazilian
- `pt-BR` (Brazilian Portuguese) and `pt-PT` (European Portuguese) have substantial phonological, lexical, and orthographic divergence.
- Generic `pt` must resolve via likely subtags or user preferences. For MT prompt instructions, specifying "Brazilian Portuguese" (`pt-BR`) or "European Portuguese" (`pt-PT`) prevents grammatical and stylistic discordance.

### 3.3 Serbian & Dual-Script Languages
- Serbian uses both Cyrillic and Latin scripts: `sr-Cyrl` vs `sr-Latn`.
- Subtitle display requires explicit script selection, as Serbian readers may strongly prefer one orthography.

### 3.4 Bidirectional & Right-To-Left (RTL) Subtitle Typography
Languages such as Arabic (`ar`), Hebrew (`he`), Persian (`fa`), and Urdu (`ur`) require comprehensive RTL layout handling:
1. **DOM Container Direction**: Subtitle containers must dynamically receive `dir="rtl"` or `dir="ltr"` based on the resolved target language.
2. **CSS Logical Properties**: Replace `left`/`right` properties in subtitle positioning with `inset-inline-start` and `inset-inline-end`.
3. **Punctuation & Mixed LTR-RTL Tokens**: Subtitles containing source names, numbers, or brand tags (e.g., "iPhone 15 在..." or English song lyrics inside Arabic speech) must enforce Unicode Bidirectional Algorithm (UBA) isolation:
   ```html
   <div class="subtitle-cue" dir="auto">
     <bdi class="cue-source">...</bdi>
     <bdi class="cue-target">...</bdi>
   </div>
   ```
4. **Direction Detection**: `Intl.Locale.prototype.getTextInfo()` provides runtime `'ltr' | 'rtl'` data in current browsers, but it remains a newly available ECMA-402 feature. LagLingo must retain `dir="auto"`/`<bdi>` and a small script fallback rather than treating this method as universally available; older implementations may expose the former `textInfo` accessor.

---

## 4. UI vs. Subtitle Target vs. Source Language

To ensure clean user ergonomics, LagLingo must maintain three distinct language contexts:
1. **Application UI Language (`ui_lang`)**: The language of the web player controls, tooltips, dialogs, and diagnostic labels.
2. **Subtitle Source Language (`source_lang`)**: The audio language spoken in the video stream (used to configure ASR acoustic models, VAD sensitivity, and MT source prompting).
3. **Subtitle Target Language (`target_lang`)**: The language into which subtitles are translated and displayed on screen.

```
+-------------------+      Stream Audio       +---------------------+
| Subtitle Source   | =====================> | ASR Engine          |
| Tag: e.g. "ja"    |                        | (Acoustic / LangID) |
+-------------------+                        +---------------------+
                                                        | Source Utterance
+-------------------+      Translated Text   +---------------------+
| Subtitle Target   | <===================== | MT Engine / LLM     |
| Tag: "zh-Hans"    |                        | (Prompt Context)    |
+-------------------+                        +---------------------+
          |
          v
+-------------------+
| Web Subtitle Layer| (dir="auto", font-family, CSS styling)
+-------------------+
```

---

## 5. Auto-Detection Policy vs. Language Tag

Treating "Auto" as a language tag (e.g. `lang="auto"`) violates BCP 47 and causes pipeline breakage. 

### 5.1 Auto as a Source Pipeline Policy
- `source_language_policy`: An operational enum `{"mode": "manual", "tag": "ja"} | {"mode": "auto"}`.
- When `mode: "auto"` is active:
  1. If the active ASR engine supports dynamic streaming language identification (e.g., Qwen Realtime reporting `event.language`), the pipeline receives candidate language tags per utterance.
  2. **Hysteresis & Anti-Flapping Filter**: A live speaker often code-switches or utters loan words. The pipeline must not switch MT translation prompts every cue. An evidence accumulator (e.g., 3 consecutive segments or a high confidence score) stabilizes the current session's dominant source language.
  3. The detected language tag is attached to the `Cue` metadata (`cue.lang = "ja"`), enabling dynamic target translation.

---

## 6. Provider Capabilities, Support Tiers & Aliasing

Different AI providers expect wildly disparate language identifiers:
- **DashScope Qwen-MT**: Expects lowercase codes or names (`"zh"`, `"en"`, `"ja"`, `"ko"`, `"ru"`, `"fr"`, `"es"`, `"it"`, `"de"`, `"tr"`, etc.).
- **OpenAI Audio API (Whisper)**: Expects ISO 639-1 two-letter codes (`"en"`, `"es"`, `"ja"`, etc.).
- **Generic LLMs (OpenAI-compatible / Qwen3.5)**: Work best with natural language display names in the prompt (e.g., `"Japanese"`, `"Simplified Chinese"`).

### 6.1 Provider Support Tiers
LagLingo categorizes language-pair support into three explicit tiers:
- **Tier 1 (Verified / Native)**: Rigorously benchmarked by LagLingo. Known latency, optimal VAD parameters, high BLEU/COMET subtitle accuracy. Displayed with a verified badge in UI.
- **Tier 2 (Provider-Claimed)**: Explicitly listed in the provider's official capabilities or documentation, but unverified by LagLingo integration tests.
- **Tier 3 (Experimental / Best-Effort)**: Accessible via generic LLM translation models with arbitrary BCP 47 prompts. Flagged with an experimental warning.

### 6.2 Matrix Intersection & Validation
When the user configures an ASR provider and an MT provider:
$$\text{Valid Targets}(P_{ASR}, P_{MT}, \text{source}) = \text{MT\_Targets}(P_{MT}) \cap \text{SupportedByPromptTemplate}$$

If a user selects an incompatible pair, the UI highlights the conflict and suggests either fallback providers or configuration adjustments.

---

## 7. Zero-Maintenance Catalog & Architecture for LagLingo

A small local Python + browser application must **not** maintain language metadata by hand. The final recommendation is a generated, pinned CLDR-derived catalog committed with the app, plus the small `langcodes` runtime for canonical BCP 47 validation; browser `Intl` localizes display labels at runtime.

### 7.1 Separation of Data Ownership
```
+---------------------------------------------------------------------------------+
| Browser Web Player (Vanilla JS)                                                 |
| - Standard Intl APIs: Intl.DisplayNames, Intl.Locale, Intl.Collator             |
| - Search Index: Native Name + Localized Name + Transliteration / Aliases        |
| - Subtitle Typography: dir="auto", Bidi Isolation (<bdi>)                       |
+---------------------------------------------------------------------------------+
                                      ^
                                      | REST / SSE API: Language Catalog View
                                      v
+---------------------------------------------------------------------------------+
| Python Companion Server (FastAPI / aiohttp)                                     |
| - Canonical BCP 47 Tag Parser & Normalizer (Zero heavy dependencies)           |
| - Provider Language Code Adapter (BCP 47 -> Provider Specific Wire Format)     |
| - Curated Preset Registry (~40 high-frequency broadcast languages)             |
+---------------------------------------------------------------------------------+
```

### 7.2 Web Browser Responsibilities
Modern evergreen browsers already embed the full Unicode CLDR database through ECMAScript `Intl`:
1. **Display Names**:
   ```javascript
   const displayInEn = new Intl.DisplayNames(['en'], { type: 'language' });
   const displayInSelf = new Intl.DisplayNames([tag], { type: 'language' });
   // Returns: "Japanese" and "日本語"
   const label = `${displayInSelf.of(tag)} (${displayInEn.of(tag)})`;
   ```
2. **Canonical Tag Normalization**:
   ```javascript
   const canonicalTag = Intl.getCanonicalLocales(inputTag)[0];
   const locale = new Intl.Locale(canonicalTag);
   ```
3. **Direction Detection**:
   ```javascript
   // Newly broadly available in current browsers; retain a catalog fallback.
   const isRTL = locale.getTextInfo ? locale.getTextInfo().direction === 'rtl' : catalogDirection === 'rtl';
   ```

### 7.3 Python Backend Responsibilities
The backend needs a lightweight, rock-solid validation and aliasing engine without bloated dependencies:
- **Generated Catalog Artifact**: A compact versioned JSON artifact generated from a pinned Unicode CLDR/IANA source set. Provider-supported tags and selected CLDR coverage levels determine which entries ship; no unsupported global-coverage percentage is claimed.
- **Provider Translation Adapter**:
  ```python
  class ProviderLanguageAdapter:
      @staticmethod
      def to_qwen_mt(bcp47_tag: str) -> str:
          tag_map = {
              "zh-Hans": "zh",
              "zh-Hant": "zh-tw",
              "yue": "yue",
              "ja": "ja",
              "en": "en",
              "ko": "ko",
              "pt-BR": "pt",
              "pt-PT": "pt",
          }
          return tag_map.get(bcp47_tag, bcp47_tag.split("-")[0])

      @staticmethod
      def to_prompt_name(bcp47_tag: str) -> str:
          # English canonical name for LLM system prompts
          names = {
              "zh-Hans": "Simplified Chinese",
              "zh-Hant": "Traditional Chinese",
              "ja": "Japanese",
              "en": "English",
              "ko": "Korean",
              "yue": "Cantonese",
              "es": "Spanish",
              "fr": "French",
              "de": "German",
          }
          return names.get(bcp47_tag, bcp47_tag)
  ```

---

## 8. Concrete Implementation Plan for LagLingo

### 8.1 Data Models (Pydantic / Dataclasses)
```python
@dataclass(frozen=True)
class LanguageDefinition:
    tag: str                  # Canonical BCP 47 (e.g., 'zh-Hans')
    english_name: str         # Standard English name
    native_name: str          # Endonym
    script: str | None = None
    region: str | None = None
    rtl: bool = False
    aliases: tuple[str, ...] = ()

@dataclass
class ProviderLanguageCapabilities:
    provider_id: str
    supported_source_tags: set[str]
    supported_target_tags: set[str]
    tier_1_pairs: set[tuple[str, str]]
    tier_2_pairs: set[tuple[str, str]]
```

### 8.2 Frontend Search & Selection Component
In `web-player/player.js`:
- Replace raw `<input>` with an accessible searchable combobox.
- Users can search by:
  - English name: `"Japanese"`, `"Chinese"`
  - Native name: `"日本語"`, `"中文"`
  - Code / Aliases: `"ja"`, `"jp"`, `"zh"`, `"cmn"`
  - Pinyin / Romaji: `"zhongwen"`, `"nihongo"`

---

## 9. Primary Standards & References

1. **IETF BCP 47 (RFC 5646)**: *Tags for Identifying Languages*. A. Phillips, M. Davis. [https://www.rfc-editor.org/rfc/bcp/bcp47.txt](https://www.rfc-editor.org/rfc/bcp/bcp47.txt)
2. **IETF RFC 4647**: *Matching of Language Tags*. A. Phillips, M. Davis. [https://www.rfc-editor.org/rfc/rfc4647.txt](https://www.rfc-editor.org/rfc/rfc4647.txt)
3. **Unicode CLDR (UTS #35)**: *Unicode Locale Data Markup Language (LDML)*. [https://unicode.org/reports/tr35/](https://unicode.org/reports/tr35/)
4. **ECMA-402**: *ECMAScript Internationalization API Specification (`Intl.DisplayNames`, `Intl.Locale`)*. [https://tc39.es/ecma402/](https://tc39.es/ecma402/)
5. **W3C Internationalization Working Group**: *Language tags in HTML and XML*. [https://www.w3.org/International/articles/language-tags/](https://www.w3.org/International/articles/language-tags/)
6. **W3C Subtitle & Bidi Requirements**: *WebRTC and Subtitle Directionality (`dir="auto"`, `<bdi>`)*. [https://www.w3.org/TR/html52/dom.html#the-dir-attribute](https://www.w3.org/TR/html52/dom.html#the-dir-attribute)
