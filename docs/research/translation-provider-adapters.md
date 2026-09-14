# First-Class Translation Provider Adapters: OpenAI, Anthropic Claude, and Google Gemini

> **Target File:** `docs/research/translation-provider-adapters.md`  
> **Date:** 2026-08-31  
> **Scope:** Architecture, canonical API contracts, usage normalization, prompt caching, error handling, and language capability strategy for real-time live subtitle machine translation (MT) in LagLingo companion service.
>
> **Verification note:** This is a preliminary subagent research artifact. The final plan keeps the existing `openai-compatible` kind for compatibility and adds first-class Anthropic/Gemini kinds rather than forcing a breaking rename. It also separates normal input, cache-read input, and cache-write input usage/pricing; Anthropic cache creation cannot be priced accurately with the existing three-rate schema. Exact model names, prices, caching thresholds, and preview endpoint versions are presets to refresh, not stable protocol contracts.

---

## 1. Executive Summary & Core Architectural Decision

### 1.1 Context & Objectives
LagLingo translates spoken live subtitles under strict real-time deadlines (median utterance duration ~1.6s, latency budget < 1000ms–2000ms per subtitle chunk). The translation engine requires:
1. **Rolling History Support:** Contextual disambiguation of subject dropping (especially in Japanese/Korean) and pronoun reference.
2. **Glossary & Domain Grounding:** Strict entity, character name, and terminology adherence.
3. **Usage & Cost Normalization:** Unified accounting for input, output, and provider-level cached tokens across billing models.
4. **Deadline & Timeout Propagation:** Millisecond-accurate cancellation without connection leaks.
5. **Robust Fallback & Fast Circuit Breaking:** Immediate failover upon rate limits, auth failure, server overload, or timeouts.

### 1.2 SDK vs. Direct Async HTTP (`aiohttp`)
**Recommendation: Direct Async HTTP via `aiohttp` across all providers.**
* **Weight & Dependency Isolation:** The companion runtime runs as an embedded local daemon (Windows/macOS/Linux) alongside browser extensions and MPV/web player hooks. Official Python SDKs (`openai`, `anthropic`, `google-genai` / `google-generativeai`) pull massive, divergent dependency trees (Pydantic v2, AnyIO, HTTPX, gRPC, proto-plus, Google Auth, etc.), creating version pin conflicts.
* **Granular Timeout & Deadline Enforcement:** Under `aiohttp`, request timeouts map directly to `asyncio.wait_for` and `aiohttp.ClientTimeout(total=timeout)` with active connection socket pooling and deterministic TCP cleanup.
* **Zero Overhead / Transparent Wire Debugging:** Direct JSON payloads match exact official REST endpoints without SDK abstraction obscurities (e.g. SDK-side `extra_body` nesting hazards).

### 1.3 Provider Kind Taxonomy Analysis: 3 Distinct Kinds vs. Generic `openai-compatible`
**Recommendation: Introduce distinct provider kinds (`openai-chat`, `anthropic-messages`, `google-genai`), while retaining `openai-compatible` (generic) and `qwen-mt` (specialized translation-memory format).**

* **Why not keep everything in `openai-compatible`?**
  * Anthropic and Google Gemini have fundamentally incompatible REST schemas:
    * Anthropic uses top-level `system` (string or array of text blocks) and rejects `system` role in `messages` (`HTTP 400: messages.role must be either 'user' or 'assistant'`). It authenticates via `x-api-key` and requires `anthropic-version`.
    * Google Gemini uses `/v1beta/models/{model}:generateContent`, authenticates via `x-goog-api-key` (or query param), formats messages as `contents: [{"role": "user", "parts": [{"text": ...}]}]`, and uses `systemInstruction: {"parts": [{"text": ...]}`.
    * Usage objects, error formats, and caching primitives differ completely across all three vendors.
* **Proposed Registry Kinds:**
  1. `openai-chat` (or generic `openai-compatible`): Standard OpenAI Chat Completions schema (`/v1/chat/completions`, `Bearer` auth, `prompt_tokens_details.cached_tokens`).
  2. `anthropic-messages`: Anthropic Messages schema (`/v1/messages`, `x-api-key`, explicit `cache_control: {"type": "ephemeral"}`, `cache_read_input_tokens`).
  3. `google-genai`: Google Gemini Developer API schema (`/v1beta/models/{model}:generateContent`, `x-goog-api-key`, `generationConfig`, `cachedContentTokenCount`).
  4. `qwen-mt`: Specialized DashScope translation-memory endpoint (`translation_options.tm_list` & `terms`).

---

## 2. Detailed Provider Specifications

### 2.1 OpenAI (`openai-chat` / `openai-compatible`)

#### A. Canonical Endpoint & Authentication
* **Canonical URL:** `https://api.openai.com/v1/chat/completions` (overridable via `baseUrl`).
* **HTTP Method:** `POST`
* **Authentication Header:** `Authorization: Bearer <OPENAI_API_KEY>`
* **Optional Headers:** `OpenAI-Organization: <ORG_ID>`, `OpenAI-Project: <PROJECT_ID>`

#### B. Request Payload Shape
```json
{
  "model": "gpt-4o-mini",
  "messages": [
    {
      "role": "system",
      "content": "你是直播字幕翻译器..."
    },
    {
      "role": "user",
      "content": "HISTORY:\n...\nCURRENT:\n..."
    }
  ],
  "temperature": 0.3,
  "max_tokens": 256,
  "stream": false
}
```

#### C. Prompt Caching Mechanism
* **Behavior:** Automatic on prefixes $\ge 1024$ tokens in 128-token increments (applicable to `gpt-4o`, `gpt-4o-mini`, etc.) *(Source: OpenAI Official API Prompt Caching Guide)*.
* **TTL:** Typically 5 to 10 minutes of inactivity; refreshed on cache hit.
* **Implementation Strategy for Subtitles:** Keep the system prompt + static stream metadata + glossary at the very beginning of the prompt sequence. For high-volume live streams where glossary/instructions exceed 1024 tokens, caching activates automatically.

#### D. Token Usage Response
```json
{
  "usage": {
    "prompt_tokens": 1250,
    "completion_tokens": 42,
    "total_tokens": 1292,
    "prompt_tokens_details": {
      "cached_tokens": 1024,
      "audio_tokens": 0
    },
    "completion_tokens_details": {
      "reasoning_tokens": 0
    }
  }
}
```

#### E. Structured Output Options
* **JSON Object Mode:** `"response_format": {"type": "json_object"}` (requires prompt to include the word "JSON").
* **Strict JSON Schema Mode:** `"response_format": {"type": "json_schema", "json_schema": {"name": "translation_response", "strict": true, "schema": {...}}}`.
* *Subtitle Recommendation:* For low latency single-sentence translation, plain text with strict system prompt formatting constraints ("Only output raw translation") is ~15-30% faster and uses fewer completion tokens than JSON schema decoding.

---

### 2.2 Anthropic Claude (`anthropic-messages`)

#### A. Canonical Endpoint & Authentication
* **Canonical URL:** `https://api.anthropic.com/v1/messages` (overridable via `baseUrl`).
* **HTTP Method:** `POST`
* **Authentication Headers:**
  * `x-api-key: <ANTHROPIC_API_KEY>`
  * `anthropic-version: 2023-06-01`
  * `content-type: application/json`

#### B. Request Payload Shape & System Instruction
Anthropic strictly separates `system` from `messages`. Placing `role: "system"` inside `messages` causes `HTTP 400 invalid_request_error`.
```json
{
  "model": "claude-3-5-haiku-20241022",
  "max_tokens": 256,
  "temperature": 0.3,
  "system": [
    {
      "type": "text",
      "text": "你是直播字幕翻译器。把 CURRENT 从日语译成中文...\n直播信息：...\n术语表：...",
      "cache_control": {
        "type": "ephemeral"
      }
    }
  ],
  "messages": [
    {
      "role": "user",
      "content": "HISTORY:\n主播: こんにちは -> 你好\nCURRENT:\nよろしくお願いします"
    }
  ]
}
```

#### C. Prompt Caching Mechanism
* **Behavior:** Explicit breakpoint tagging using `"cache_control": {"type": "ephemeral"}` *(Source: Anthropic Claude API Documentation)*.
* **Minimum Token Threshold:**
  * Claude 3.5 Sonnet / Opus: 1,024 tokens.
  * Claude 3.5 Haiku: 2,048 tokens.
* **TTL:** 5 minutes (refreshed each time the cache breakpoint is hit).
* **Cost Structure:**
  * Cache Write: 1.25× base input token price.
  * Cache Read: 0.10× base input token price (90% discount).
* **Implementation Strategy for Subtitles:**
  Mark the static `system` block (which contains task instructions, stream metadata, and glossary) with `cache_control: {"type": "ephemeral"}`. Keep dynamic rolling `HISTORY` and `CURRENT` in `messages[0]` without cache tags to ensure cache prefix stability.

#### D. Token Usage Response
```json
{
  "usage": {
    "input_tokens": 25,
    "output_tokens": 12,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 1050
  }
}
```
*Note:* When a cache hit occurs, `input_tokens` reflects only the non-cached tokens (or remaining uncached delta), while `cache_read_input_tokens` reports the cached segment.

---

### 2.3 Google Gemini (`google-genai`)

#### A. Canonical Endpoint & Authentication
* **Canonical URL:** `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` (overridable via `baseUrl`).
* **HTTP Method:** `POST`
* **Authentication Header:** `x-goog-api-key: <GEMINI_API_KEY>` (or query parameter `?key=<KEY>`).
* **Header:** `Content-Type: application/json`

#### B. Request Payload Shape
Google Gemini Developer REST API uses `systemInstruction` and `contents` with nested `parts`.
```json
{
  "systemInstruction": {
    "parts": [
      {
        "text": "你是直播字幕翻译器。把 CURRENT 从日语译成中文...\n术语表：..."
      }
    ]
  },
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "HISTORY:\n...\nCURRENT:\n..."
        }
      ]
    }
  ],
  "generationConfig": {
    "temperature": 0.3,
    "maxOutputTokens": 256,
    "responseMimeType": "text/plain"
  }
}
```

#### C. Prompt Caching Mechanism
* **Behavior:** Gemini API supports explicit Context Caching via `/v1beta/cachedContents` REST resource *(Source: Google AI for Developers Caching Guide)*.
* **Minimum Token Threshold:** Minimum 32,768 tokens (for `gemini-1.5-flash` / `gemini-2.5-flash` / `gemini-1.5-pro`).
* **Live Subtitle Relevance:** Because live subtitle prompt templates (system + glossary + 10 turns of history) are typically 300–1,500 tokens, Gemini explicit cached contents are inapplicable to single short streams unless a massive stream knowledge base (>32k tokens) is pre-cached with a TTL. Gemini implicit server-side caching (where supported in newer v1beta tiers) is automatically reported in `usageMetadata`.

#### D. Token Usage Response (`usageMetadata`)
```json
{
  "usageMetadata": {
    "promptTokenCount": 1120,
    "candidatesTokenCount": 18,
    "totalTokenCount": 1138,
    "cachedContentTokenCount": 1024,
    "thoughtsTokenCount": 0
  }
}
```
*Important Accounting Invariant:* In Gemini API, `promptTokenCount` is the **total** input token count (inclusive of `cachedContentTokenCount`). Therefore:
$$\text{Uncached Input Tokens} = \text{promptTokenCount} - \text{cachedContentTokenCount}$$

#### E. Response Parsing
The generated translation text is extracted from:
`data["candidates"][0]["content"]["parts"][0]["text"]`

---

## 3. Normalized Data Contracts

### 3.1 Normalized Token Usage Contract
To allow uniform logging, metrics, cost calculation, and telemetry across all providers:

```python
@dataclasses.dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_creation_tokens: int = 0
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def total_billable_input(self) -> int:
        """Effective uncached billable input tokens."""
        return max(0, self.input_tokens - self.cached_input_tokens)
```

#### Normalization Mapping Rules:
1. **OpenAI:**
   * `input_tokens = data["usage"]["prompt_tokens"]`
   * `output_tokens = data["usage"]["completion_tokens"]`
   * `cached_input_tokens = data["usage"].get("prompt_tokens_details", {}).get("cached_tokens", 0)`
   * `cache_creation_tokens = 0`
2. **Anthropic Claude:**
   * `cached_input_tokens = data["usage"].get("cache_read_input_tokens", 0)`
   * `cache_creation_tokens = data["usage"].get("cache_creation_input_tokens", 0)`
   * `input_tokens = data["usage"]["input_tokens"] + cached_input_tokens + cache_creation_tokens` (normalizes to total prompt size)
   * `output_tokens = data["usage"]["output_tokens"]`
3. **Google Gemini:**
   * `input_tokens = data["usageMetadata"].get("promptTokenCount", 0)`
   * `output_tokens = data["usageMetadata"].get("candidatesTokenCount", 0)`
   * `cached_input_tokens = data["usageMetadata"].get("cachedContentTokenCount", 0)`
   * `cache_creation_tokens = 0`

---

### 3.2 Normalized Error Taxonomy & Circuit Breaking

When errors occur during live translation, exceptions must be normalized into semantic classes so the `FallbackChain` can make deterministic failover and cooling decisions:

```
                      TranslationError (Base)
                                 |
     +---------------------------+---------------------------+
     |                           |                           |
AuthOrConfigError         TransientRateLimitError    TransientServerError
(Non-retryable /          (429 / Quota /             (500, 502, 503, 504, 529 /
 Immediate Cooldown)       Acceleration)              Network Timeout)
     |
InvalidRequestError (400 schema error)
```

#### HTTP Status Code & Error Mapping Table

| Provider | Auth / Permission (Cooldown / Don't retry) | Rate Limit (Transient Fallback) | Server Overload / Internal (Transient) | Format / Schema Error |
|---|---|---|---|---|
| **OpenAI** | `401 unauthorized`, `403 forbidden`, `invalid_api_key` | `429 rate_limit_exceeded`, `insufficient_quota` | `500 internal_server_error`, `503 service_unavailable` | `400 invalid_request_error` |
| **Anthropic** | `401 authentication_error`, `403 permission_error` | `429 rate_limit_error` | `500 api_error`, `529 overloaded_error` *(Anthropic specific)* | `400 invalid_request_error` |
| **Google Gemini** | `400 API_KEY_INVALID`, `403 PERMISSION_DENIED` | `429 RESOURCE_EXHAUSTED` | `500 INTERNAL`, `503 UNAVAILABLE`, `504 DEADLINE_EXCEEDED` | `400 INVALID_ARGUMENT` |
| **aiohttp / OS** | N/A | N/A | `ClientConnectorError`, `asyncio.TimeoutError`, `ServerDisconnectedError` | N/A |

#### Fallback Chain Cooling Policy:
* **Immediate Cooldown (60s+):** `AuthOrConfigError` (bad key, invalid account), `InvalidRequestError`.
* **Standard Consecutive Cooldown (threshold = 3 failures):** `TransientServerError`, `asyncio.TimeoutError`.
* **Instant Fallback with Fast Probe:** `429 RateLimitError`, `529 OverloadedError` (Anthropic).

---

## 4. Prompt Engineering, Rolling History & Language Capability Strategy

### 4.1 System & User Prompt Splitting for Live Translation

Live translation demands zero hallucinations, preserved subtitle cadence, strict adherence to terminology, and clean separation between stable instructions and rolling history.

#### 1. System Prompt (Stable Prefix):
```
你是直播字幕翻译器。把 CURRENT 从{source_lang}译成{target_lang}。
规则：
1. 只输出 CURRENT 的译文，不要输出解释、不要重复 HISTORY。
2. HISTORY 只用于理解指代、省略主语和话题，不要翻译它。
3. 译文要像直播字幕：简洁、口语、可一眼读完。
4. 不要补全说话人没说完的内容，不要添加未表达的事实。
5. 人名/专有名词严格遵循术语表。
6. 只输出译文本身，不加引号、不加前缀。
直播信息：{title} / {channel} / 领域：{domain}
术语表：
{glossary_lines}
```

#### 2. User Prompt (Dynamic per Utterance):
```
HISTORY:
{history_lines}
CURRENT:
{source_text}
```

*Note on History Ingestion:*  
While OpenAI and Gemini can parse structured chat turns (`role: "user"`, `role: "assistant"`), formatting prior turns in a unified text block under `HISTORY:` within a single user turn prevents chat models from attempting multi-turn conversational roleplay and consistently yields lower TTFT (time-to-first-token).

---

### 4.2 Language Capability Metadata Strategy

LLMs accept arbitrary language names in prompts, but model translation fidelity varies significantly across language pairs. Furthermore, specialized MT engines (such as `qwen-mt`) require ISO codes or predefined language matrices.

#### Language Capability Specification:
1. **Capabilities Matrix in `TranslationCapabilities`:**
```python
@dataclasses.dataclass(frozen=True)
class TranslationCapabilities:
    rolling_context: bool
    glossary: bool
    domains: bool
    json_output: bool
    max_input_chars: int
    supported_source_languages: tuple[str, ...] | None = None  # None = open-world prompt-based
    supported_target_languages: tuple[str, ...] | None = None
```
2. **Language Code Mapping:**
   * Standard LLMs (`gpt-4o-mini`, `claude-3-5-haiku`, `gemini-2.5-flash`): Use localized display names in prompts (`{"ja": "日语", "zh": "中文", "en": "英语", "ko": "韩语", "de": "德语", "fr": "法语", "es": "西班牙语", "ru": "俄语"}`).
   * Specialized MT Models (`qwen-mt`): Mapped to strict English language tokens (`{"ja": "Japanese", "zh": "Chinese", "en": "English", "ko": "Korean"}`).
3. **Pre-flight Capability Validation:**  
   Before dispatching a translation request, `companion/subtitle_pipeline.py` validates `(source_lang, target_lang)` against `provider.capabilities.supported_source_languages`. If unsupported, `FallbackChain` bypasses the provider without incurring network latency.

---

## 5. Configuration & Model Settings Schema

### 5.1 Updated `providers.json` Configuration Structure

```json
{
  "version": 2,
  "translation": {
    "active": "anthropic-haiku",
    "fallback": ["openai-4o-mini", "gemini-flash", "bailian-qwen-mt-flash"],
    "providers": [
      {
        "id": "anthropic-haiku",
        "label": "Anthropic Claude 3.5 Haiku",
        "kind": "anthropic-messages",
        "model": "claude-3-5-haiku-20241022",
        "baseUrl": "https://api.anthropic.com/v1",
        "apiKeyEnv": "ANTHROPIC_API_KEY",
        "pricePerMillionInputTokensCny": 5.8,
        "pricePerMillionCachedInputTokensCny": 0.58,
        "pricePerMillionOutputTokensCny": 29.0,
        "options": {
          "temperature": 0.3,
          "maxTokens": 256,
          "timeoutSeconds": 4.0,
          "enablePromptCaching": true
        }
      },
      {
        "id": "openai-4o-mini",
        "label": "OpenAI GPT-4o-mini",
        "kind": "openai-chat",
        "model": "gpt-4o-mini",
        "baseUrl": "https://api.openai.com/v1",
        "apiKeyEnv": "OPENAI_API_KEY",
        "pricePerMillionInputTokensCny": 1.08,
        "pricePerMillionCachedInputTokensCny": 0.54,
        "pricePerMillionOutputTokensCny": 4.32,
        "options": {
          "temperature": 0.3,
          "maxTokens": 256,
          "timeoutSeconds": 4.0
        }
      },
      {
        "id": "gemini-flash",
        "label": "Google Gemini 2.5 Flash",
        "kind": "google-genai",
        "model": "gemini-2.5-flash",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
        "apiKeyEnv": "GEMINI_API_KEY",
        "pricePerMillionInputTokensCny": 0.54,
        "pricePerMillionCachedInputTokensCny": 0.135,
        "pricePerMillionOutputTokensCny": 2.16,
        "options": {
          "temperature": 0.3,
          "maxTokens": 256,
          "timeoutSeconds": 4.0
        }
      },
      {
        "id": "bailian-qwen-mt-flash",
        "label": "百炼 Qwen-MT-Flash（专用极速备线）",
        "kind": "qwen-mt",
        "model": "qwen-mt-flash",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "apiKeyEnv": "DASHSCOPE_API_KEY",
        "options": {
          "timeoutSeconds": 3.0,
          "tmPairs": 4
        }
      }
    ]
  }
}
```

---

## 6. Verification Plan & Next Steps

1. **Unit Testing Strategy:**
   * Mock HTTP responses for OpenAI, Anthropic, and Google Gemini using `aiohttp` test utilities (`aioresponses` or custom mock server).
   * Verify header generation (`x-api-key`, `anthropic-version`, `x-goog-api-key`, `Authorization: Bearer`).
   * Validate token usage normalization under cache hit, cache miss, and cache write conditions across all 3 providers.
   * Verify error handling and classification (`429`, `529`, `401`, connection timeout) into proper `FallbackChain` health states.
2. **End-to-End Latency Benchmark:**
   * Run `scripts/mt-bench.py` against live models (`gpt-4o-mini`, `claude-3-5-haiku-20241022`, `gemini-2.5-flash`) comparing TTFT, P95 latency, and output consistency on Japanese $\rightarrow$ Chinese streaming subtitle datasets.
