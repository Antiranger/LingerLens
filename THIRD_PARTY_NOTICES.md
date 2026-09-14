# Third-party notices

LagLingo is MIT-licensed, but it redistributes or interoperates with third-party software under each project's own terms.

## Redistributed files

- **yt-dlp** — `prototype/hls-companion/vendor/yt-dlp/yt-dlp.exe`, version `2026.08.19`. yt-dlp is licensed under The Unlicense. Source and notices: <https://github.com/yt-dlp/yt-dlp>.
- **hls.js** — `prototype/hls-companion/web-player/vendor/hls.min.js`, version `1.7.1`. hls.js is licensed under Apache License 2.0. Source and license: <https://github.com/video-dev/hls.js>.

The yt-dlp executable is integrity-pinned by the adjacent SHA-256 file and verified by `bootstrap.ps1`.

### Bundled web fonts

`prototype/hls-companion/web-player/fonts/` and the generated `fonts.css` alongside it
redistribute three font families, all under the **SIL Open Font License 1.1**, obtained
unmodified from the `@fontsource` packages at the pinned version recorded in
`scripts/fetch-fonts.py`. They are shipped as `.woff2` split by `unicode-range`; the
families, their copyright holders and their upstream projects are:

- **Noto Sans SC** — © The Noto Project Authors. <https://github.com/notofonts/noto-cjk>, <https://fonts.google.com/noto/specimen/Noto+Sans+SC>.
- **Archivo Black** — © Omnibus-Type. <https://github.com/Omnibus-Type/ArchivoBlack>.
- **JetBrains Mono** — © JetBrains s.r.o. <https://github.com/JetBrains/JetBrainsMono>.

The full license text is the SIL Open Font License 1.1:
<https://openfontlicense.org/open-font-license-official-text/> and
<https://scripts.sil.org/OFL>. The fonts are redistributed unmodified and are not sold
on their own; the Reserved Font Name clause therefore places no additional obligation on
this project beyond keeping the notices and license with the font files. Regenerate or
verify the set with `python scripts/fetch-fonts.py` (`--check` verifies every declared
`@font-face` is backed by a file).

## Desktop distribution

The optional Windows desktop build additionally redistributes Electron (including
Chromium and Node.js), frozen Python and its packages/dictionary, and a pinned
FFmpeg/ffprobe build. See [desktop/DEPENDENCIES.md](desktop/DEPENDENCIES.md) for
the bundled notices, exact build provenance and pre-publication source obligations.
The Gyan FFmpeg essentials build is GPL v3, not an LGPL-only distribution.
The system-dependency statement below describes browser/developer mode only.

## Installed/runtime dependencies

- **aiohttp** and its transitive Python dependencies are installed from PyPI using `prototype/hls-companion/companion/requirements.txt`; see <https://github.com/aio-libs/aiohttp>.
- **langcodes** (MIT) is installed from PyPI via the same requirements file and provides server-side BCP 47 validation/canonicalization; see <https://github.com/rspeer/langcodes>. The checked-in `companion/data/languages.json` catalog is generated from langcodes + `language_data` (CLDR-derived, MIT) by `scripts/generate-language-catalog.py`; CLDR data is used under the Unicode License <https://www.unicode.org/license.txt>.
- **fugashi** (MIT; verified version 1.4.0) provides local Japanese morphology via MeCab. Its wheel includes MeCab's BSD notice (`LICENSE.mecab`); see <https://github.com/polm/fugashi>.
- **unidic-lite** (verified version 1.0.8) supplies the Japanese dictionary. The Python packaging is MIT; the bundled UniDic data carries the UniDic Consortium BSD notice (`LICENSE.unidic`; dictionary `COPYING` also lists GPL/LGPL/BSD alternatives). These packages are installed from PyPI through the same requirements file, with their original notices; see <https://github.com/polm/unidic-lite>.
- **FFmpeg/ffprobe**, Python, Node.js/npm, and Chrome or Edge are system dependencies and are not redistributed by this repository. Their respective licenses and installation terms apply.

## External services and sites

LagLingo can connect to YouTube, Bilibili, and user-configured ASR/translation providers. Those services are not part of LagLingo. Users are responsible for their accounts, credentials, content rights, service terms, regional restrictions, and provider charges. LagLingo does not bypass DRM or paid entitlements.

## Protocol documentation sources

LagLingo's provider Adapters are re-implemented from official public protocol documentation without copying provider or SDK source code:

- **Deepgram Streaming** (`deepgram-streaming`): <https://developers.deepgram.com/reference/speech-to-text/listen-streaming>, <https://developers.deepgram.com/docs/lower-level-websockets>, <https://developers.deepgram.com/docs/multilingual-code-switching>, <https://developers.deepgram.com/docs/languages-overview>.
- **OpenAI Realtime Transcription** (`openai-realtime-transcription`, current `type: "transcription"` session schema, `gpt-live-transcribe` / `gpt-transcribe`): <https://developers.openai.com/api/docs/guides/realtime-transcription>.
- **Alibaba DashScope Fun-ASR / Qwen-Audio-3.0-ASR-Flash-Streaming realtime** (`dashscope-task-asr` preset refresh; protocol unchanged): <https://help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api>, <https://help.aliyun.com/zh/model-studio/fun-asr-server-events>, <https://www.alibabacloud.com/help/zh/model-studio/asr-model>.
- **Soniox Realtime STT** (`soniox-realtime`): <https://soniox.com/docs/stt/api-reference/websocket-api>, <https://github.com/soniox/soniox-python>.
- **AssemblyAI Streaming v3** (`assemblyai-streaming`): <https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket>, <https://www.assemblyai.com/docs/streaming/message-sequence>.
- **Volcano Engine large-model streaming ASR v3** (`volcengine-sauc`): <https://docs.volcengine.com/docs/6561/1354869>, <https://www.volcengine.com/docs/6561/1395846>.
- **ElevenLabs Scribe v2 Realtime** (`elevenlabs-scribe-realtime`): <https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime>.
- **Speechmatics Realtime v2** (`speechmatics-realtime`): <https://docs.speechmatics.com/api-ref/realtime-transcription-websocket>.
- **Tencent Cloud Realtime ASR** (`tencent-asr`): <https://cloud.tencent.com/document/product/1093/48982>, <https://cloud.tencent.com/document/product/1093/131127>, <https://cloud.tencent.com/document/product/1093/130881>.
- **Anthropic Messages API** (`anthropic-messages` translation Adapter): <https://platform.claude.com/docs/en/api/messages/create>, <https://platform.claude.com/docs/en/api/errors>.
- **Google Gemini GenerateContent REST API** (`google-genai` translation Adapter): <https://ai.google.dev/api/generate-content>.
