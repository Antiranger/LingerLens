# 01 — BCP 47 语言合同、配置迁移与选择 UI

**What to build:** 建立一次后续 Provider 不再需要修改的语言基础：BCP 47、SourceLanguagePolicy、Provider 语言能力、配置 v3、可搜索源/目标语言选择器、RTL/bidi，以及 Pipeline 的 16/24 kHz 与 cue 语言传递。

**Blocked by:** None — can start immediately.

**Status:** done (2026-09-01) — full `npm run ci` green; live browser smoke on the local Companion verified the searchable selectors, detect candidates, capability hints, pre-start rejection unit tests and persistence through `/api/providers`.

- [x] 先写失败测试：v2 `ja → zh` 配置迁移为 v3 specified `ja → zh-Hans`，并完整保留 profiles、active/fallback、Key、价格和字幕偏好。
- [x] 新增 `langcodes`、`companion/languages.py` 和生成后的紧凑 `languages.json`；覆盖 tag canonicalization、非法 tag、script/region 区分和 alias policy。
- [x] 在 Provider base 中加入最小 `SourceLanguagePolicy` 与 ASR/翻译语言能力字段；未知模型默认 experimental，不得伪造 auto/code-switching。
- [x] `ASRProvider.stream` 接收 policy 与 sample rate；SubtitlePipeline 支持 16/24 kHz，PCM 时钟与费用秒数保持正确，现有 16 kHz Provider 不回归。
- [x] final cue 优先保存 Adapter 报告的 canonical language；翻译请求使用 cue language，未报告时回退 specified/preferred。
- [x] loopback API 返回语言 catalog 与 active Provider 的有效能力，并在 start 前拒绝不支持的 source policy/target pair。
- [x] UI 增加指定/自动源语言、候选语言、目标语言搜索器；名称、autonym、tag 均可搜索并支持键盘操作。
- [x] 显示模式改为“原文+译文 / 仅译文 / 仅原文”；源文和译文分别 `dir="auto"` + bidi isolation，旧浏览器使用 catalog direction。
- [x] 新增 language/config/pipeline/browser 测试；现有 provider/server/pipeline/web 测试保持绿色。

## Implementation notes

- `companion/languages.py`：canonicalize（`eng_US→en-US`、`iw→he`、zh 地区→`zh-Hans/zh-Hant` 产品 alias policy）、`canonicalize_target_tag`（裸 `zh→zh-Hans`）、catalog 查询与 direction fallback。`language_data` 仅生成期需要，运行时不依赖。
- `scripts/generate-language-catalog.py` + checked-in `companion/data/languages.json`（99 条，CLDR 1.4.0，可重现生成；重新生成输出逐字节一致）。
- `providers/base.py`：`SourceLanguagePolicy`（specified/detect，preferred ∈ candidates，auto 永不为 tag）、`ASRLanguageCapabilities`/`TranslationLanguageCapabilities`（tier: verified/provider_claimed/experimental）、`validate_source_policy`/`validate_translation_pair`（BCP 47 basic-range：未限定 supported tag `zh` 覆盖 `zh-Hans`）。
- 能力 preset：Fun-ASR verified、paraformer/qwen-realtime/whisper provider_claimed；未知模型 experimental 且 detection/code-switching 为否。
- `ASRProvider.stream(policy=, sample_rate=, ...)`；三个内置 Adapter 全部迁移，Provider 报告语言在 Adapter 边缘 canonicalize。
- Pipeline：`sample_rate` 实例持有（仅 16/24 kHz），`pcm_bytes_per_second` 驱动 PCM 时钟与 ASR 费用秒数；翻译请求 `meta.source_lang` 来自 cue。
- 配置 v3：`migrate_v2_config` 把 `sourceLanguage: "ja"`→specified policy、`targetLanguage: "zh"`→`zh-Hans`；`update_config` 的 subtitle patch 在持久化前 canonicalize。
- Loopback：`GET /api/languages`（catalog + active ASR/翻译有效能力 + 当前默认）；`LanguageNotSupportedError` 在 `/api/start` 直接 400，不退化为无声字幕失败。
- UI：`web-player/language-selector.js`（WAI-ARIA combobox：Arrow/Home/End/Enter/Escape，按本地化名称/English name/autonym/tag/alias 搜索，常用语言置顶）；detect 候选 chips（首个即 preferred）；能力提示行；显示模式通用文案；字幕行 `dir=auto` + `unicode-bidi: isolate` + `data-direction` catalog fallback。
- 持久化走既有 `/api/providers` subtitle seam（与 model-settings 的所有权边界一致）；start 请求只携带语言设置，不改 active Provider 身份。
