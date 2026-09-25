# Third-party notices

LingerLens itself is MIT-licensed (see [`LICENSE`](LICENSE)). It redistributes other
people's software under their own terms, listed here.

**This file is enforced.** `npm run guard:licences` fails when something is
redistributed that is not named below, and `npm run guard:licences:frozen` extends
that to every Python distribution frozen into the desktop backend. It exists
because this file had already drifted once: it claimed `fugashi` 1.4.0 while the
build pinned 1.5.2, and `yt-dlp.exe` had grown a copyleft dependency nobody had
listed. A notice file nobody re-reads is not compliance, it is a hope.

Verbatim licence texts live in [`licenses/`](licenses/). Some cannot live only
there: the SIL Open Font License and the Apache License both require the text to
travel *with* the copy, so those also sit beside the files they cover.

---

## A. Redistributed by the source repository

Anything tracked in git is redistributed the moment the repository is published,
independently of any installer.

### yt-dlp `2026.08.19`

`prototype/hls-companion/vendor/yt-dlp/yt-dlp.exe` — **The Unlicense**.
The executable is not committed; `scripts/fetch-yt-dlp.py` downloads it from the
upstream release. SHA-256 is pinned in `desktop/dependencies.json` and the adjacent
`yt-dlp.exe.sha256`, and verified by `bootstrap.ps1` and by `desktop/prepare_backend.py` at build time. It matches the
upstream release's own `SHA2-256SUMS`, so this is the genuine upstream artifact
rather than a local rebuild.

It is a PyInstaller one-file bundle of **1,584 modules**, and the executable as a
whole is what gets redistributed. Its own upstream
[`THIRD_PARTY_LICENSES.txt`](prototype/hls-companion/vendor/yt-dlp/THIRD_PARTY_LICENSES.txt)
is shipped beside it and aggregates the texts. The components:

| Component | Version | Licence |
|---|---|---|
| **mutagen** | **1.48.1** | **GPL-2.0-or-later** |
| requests | 2.34.2 | Apache-2.0 |
| urllib3 | 2.7.0 | MIT |
| certifi (+ `cacert.pem`, 121 certs) | 2026.7.22 | MPL-2.0 |
| charset-normalizer | 3.5.0 | MIT |
| idna | 3.18 | BSD-3-Clause |
| websockets | 16.1.1 | BSD-3-Clause |
| curl_cffi | 0.16.0 | MIT |
| cffi | 2.1.1 | **MIT-0** (no attribution clause) |
| pycparser | 3.0 | BSD-3-Clause |
| pycryptodomex | 3.23.0 | Unlicense **AND** BSD-2-Clause |
| brotli | 1.2.0 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| yt-dlp-ejs (bundles Meriyah, Astring) | 0.8.0 | Unlicense AND MIT AND ISC |
| Python | 3.10.11 | PSF-2.0 |
| OpenSSL, libcurl (curl-impersonate), SQLite | 1.1.1t / 8.21.0 / 3.40.1 | OpenSSL / curl / Public Domain |
| BoringSSL, nghttp2, ngtcp2, nghttp3, zstd, zlib, bzip2, liblzma, mpdecimal, Expat, libffi | statically linked | Apache-2.0 / MIT / BSD-3 / Zlib / bzip2-1.0.6 / 0BSD / BSD-2 / MIT |

Two caveats on the upstream file, recorded so nobody trusts it blindly:

- It is a **superset across build variants.** It lists GNU Readline, ncurses,
  libidn2, libunistring, Tornado, httpx and others that are provably absent from
  this Windows x64 binary. Over-noticing is the safe direction, so it is kept
  as-is.
- It **omits `typing-extensions` 4.16.0**, which *is* bundled (that release
  defines no `__version__`, which is likely why upstream missed it). Its licence
  is PSF-2.0, already in [`licenses/PSF-2.0.txt`](licenses/PSF-2.0.txt).

**`mutagen` is the only strong copyleft component in this project** and it is
inside a binary we did not build. See [Corresponding source](#corresponding-source).

### hls.js `1.7.1`

`prototype/hls-companion/web-player/vendor/hls.min.js` — **Apache-2.0**. Licensed
text travels beside it as `LICENSE.hls.js`, which is upstream's own file and
carries two attribution notices that the previous version of this document
omitted entirely, both required by Apache-2.0 §4:

- `Copyright (c) 2017 Dailymotion`
- `src/remux/mp4-generator.js` and `src/demux/exp-golomb.ts` are derived from
  videojs-contrib-hls, `Copyright (c) 2013-2015 Brightcove`

The bundle also embeds **regenerator-runtime** (MIT, `Copyright (c) 2014-present
Sebastian McKenzie and other contributors`), recorded in
[`licenses/MIT-regenerator-runtime.txt`](licenses/MIT-regenerator-runtime.txt)
using the source URL the bundle itself names.

### Bundled web fonts

`prototype/hls-companion/web-player/fonts/` redistributes three families under
the **SIL Open Font License 1.1**, obtained unmodified from the `@fontsource`
packages at the version pinned in `scripts/fetch-fonts.py`. The licence text and
each family's copyright line are in
[`web-player/fonts/OFL.txt`](prototype/hls-companion/web-player/fonts/OFL.txt) —
beside the fonts, because OFL condition 2 requires the text to travel with them
rather than merely exist in the repository.

| Family | Copyright line (verbatim from upstream) |
|---|---|
| Noto Sans SC | `Google Inc.` |
| Archivo Black | `Copyright 2017 The Archivo Black Project Authors (https://github.com/Omnibus-Type/ArchivoBlack)` |
| JetBrains Mono | `Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)` + the italic face's longer line |

None of the three declares a **Reserved Font Name**, so redistributing them
unmodified under their own names is permitted; the file renaming in
`fetch-fonts.py` is filename-only and does not change any font's internal name.

---

## B. Redistributed by the desktop build

### FFmpeg / ffprobe `9.0.1` — LGPL v3

The bundled `ffmpeg.exe` and `ffprobe.exe` are the **BtbN `win64-lgpl`** build
(`n9.0.1-29-gad500d59cb`, autobuild `2026-09-14-13-17`), redistributed as
**LGPL v3**. `desktop/dependencies.json` pins the URL and a full SHA-256;
`desktop/prepare_backend.py` refuses to package on a mismatch.

This is verifiable from the shipped binary rather than from this sentence: the
build's own `configuration:` line is written to
`backend/_internal/third-party/ffmpeg/BUILD-CONFIGURATION.txt` at package time.
It contains `--enable-version3` and **no** `--enable-gpl`, **no**
`--enable-nonfree`, and `--disable-libx264 --disable-libx265 --disable-libxvid`.

An earlier build used the Gyan `essentials` distribution, which **is GPL v3** and
carries the whole-program source obligation that comes with it. It was replaced
because LingerLens never encodes: the product's only FFmpeg invocation is
`-c copy` (`companion/core.py`, `build_ffmpeg_command`), a stream copy that needs
no encoder at all. The `libx264` references in the tree are in smoke-test scripts
that synthesize local test input and are not part of the product path. Media
chain equivalence was verified against the LGPL build: H.264+AAC MPEG-TS input,
`-c copy`, the `aac_adtstoasc` bitstream filter, and fMP4 HLS event-playlist
output all behave identically.

### Electron `44.2.0` (Chromium + Node.js)

MIT, plus the usual Chromium/BSD-3 and permissively licensed third-party set. The
application ships `LICENSE.electron.txt` and the full
`LICENSES.chromium.html` alongside itself, generated by electron-builder.

### Frozen Python runtime and packages

Python **3.11.15** (PSF-2.0) plus its packages: `aiohttp` (Apache-2.0, including
its vendored `llhttp`), `aiohappyeyeballs`, `aiosignal`, `attrs`, `frozenlist`,
`multidict`, `propcache`, `yarl`, `idna`, `typing_extensions`, `langcodes`
(MIT), `fugashi` **1.5.2** (MIT, plus MeCab's BSD-3 notice), `unidic-lite` 1.0.8
(MIT packaging). Their `*.dist-info/licenses/` metadata ships with them.

**UniDic / unidic-mecab dictionary `2.1.2`** is triple-licensed
**GPL-2.0 / LGPL-2.1 / BSD-3-Clause**, with all three texts shipped in
`_internal/unidic_lite/dicdir/`.

> **LingerLens elects the BSD-3-Clause option** for the UniDic data. This election
> is stated here deliberately: without it a recipient could reasonably read the
> 260 MB dictionary as LGPL or GPL. Under BSD-3-Clause the obligation is
> attribution only. See [`licenses/BSD-3-Clause-unidic.txt`](licenses/BSD-3-Clause-unidic.txt).

### Native libraries pulled in by the frozen runtime

| Library | Licence | Text |
|---|---|---|
| OpenSSL 3.5.7 (`libcrypto-3-x64.dll`, `libssl-3-x64.dll`) | Apache-2.0 | [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt) |
| libffi (`libffi-8.dll`) | MIT | [`licenses/MIT-libffi.txt`](licenses/MIT-libffi.txt) |
| MS VC++ runtime / UCRT | Microsoft redistributable terms, not OSS | — |

### `elevate.exe`

Injected by electron-builder's NSIS target (`© 2007 Johannes Passing`,
<http://int3.de/>). **Its licence could not be established**: the upstream
repository publishes no `LICENSE` file at `master` or `main`, and the binary
embeds no licence text. A third-party redistribution asserts MIT with unfilled
placeholders. Flagged rather than guessed — this is unresolved.

---

## Corresponding source

Redistributing a copyleft binary makes *us* the distributor, so an upstream
project's own offer does not discharge the obligation. yt-dlp's bundled
`THIRD_PARTY_LICENSES.txt` offers source from `maintainers@yt-dlp.org`; that
offer is yt-dlp's, and it covers yt-dlp. It does not cover our redistribution.

**Written offer, valid for three years from the date of the release that
contains them.** LingerLens will provide the complete corresponding source for the
copyleft components it redistributes, on request:

| Component | Copyleft | Corresponding source |
|---|---|---|
| `mutagen` 1.48.1, inside `yt-dlp.exe` | GPL-2.0-or-later | <https://github.com/quodlibet/mutagen> at tag `1.48.1`; also on PyPI as `mutagen==1.48.1` |
| `yt-dlp.exe` as distributed (unmodified upstream PyInstaller build `2026.08.19`) | as above, via mutagen | <https://github.com/yt-dlp/yt-dlp> at tag `2026.08.19`, plus that tag's `bundle/` PyInstaller spec and `bundle/requirements/win-x64-pyinstaller.txt` |
| `certifi`'s `cacert.pem` | MPL-2.0 | <https://github.com/certifi/python-certifi> at tag `2026.7.22` |
| FFmpeg `9.0.1` | LGPL v3 | <https://github.com/FFmpeg/FFmpeg> at the commit named in the shipped binary's version string, plus BtbN's reproducible build scripts at <https://github.com/BtbN/FFmpeg-Builds> |

Requests should go to the project's issue tracker. The Artifact is distributed
unmodified, so the upstream sources above are the corresponding source; no
modification was made to any copyleft component.

---

## Installed / runtime dependencies (not redistributed)

In browser/developer mode these are the user's own installations and none of
this file's redistribution terms apply:

- **aiohttp** and its transitive dependencies, from PyPI via
  `prototype/hls-companion/companion/requirements.txt`.
- **langcodes** (MIT) supplies BCP 47 validation. The checked-in
  `companion/data/languages.json` catalog is generated by
  `scripts/generate-language-catalog.py` from langcodes plus `language_data`
  (CLDR-derived, MIT).
- **FFmpeg/ffprobe**, Python, Node.js/npm, and Chrome or Edge.

## External services and sites

LingerLens can connect to YouTube, Bilibili, Twitch, and user-configured
ASR/translation providers. Those services are not part of LingerLens. Users are
responsible for their accounts, credentials, content rights, service terms,
regional restrictions, and provider charges. LingerLens does not bypass DRM or
paid entitlements.

## Protocol documentation sources

LingerLens's provider Adapters are re-implemented from official public protocol
documentation without copying provider or SDK source code:

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
