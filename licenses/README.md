# Third-party licence texts

Verbatim licence texts for everything LagLingo redistributes. Every file here is either a copy of an authoritative artifact that already ships in this repository, or the verbatim body of a canonical upstream URL. No licence text in this directory was written from memory.

`npm run guard:licences` (`scripts/check-licence-coverage.js`) reads this directory and fails if any of the texts it requires is missing or does not look like the licence it claims to be.

## Index

| File | SPDX | Covers | Source | Bytes |
| --- | --- | --- | --- | ---: |
| [`Apache-2.0.txt`](Apache-2.0.txt) | `Apache-2.0` | aiohttp, aiosignal, frozenlist, multidict, propcache, yarl (frozen Python backend); hls.js 1.7.1; OpenSSL 3.0.15 (`libcrypto-3-x64.dll`, `libssl-3-x64.dll`) | shipped: `release/win-unpacked/resources/backend/_internal/propcache-0.5.2.dist-info/licenses/LICENSE` | 11,358 |
| [`GPL-2.0.txt`](GPL-2.0.txt) | `GPL-2.0-only` | mutagen (bundled inside `yt-dlp.exe`); UniDic dictionary data (the GPL option of its triple licence) | fetched: <https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt> | 17,984 |
| [`GPL-3.0.txt`](GPL-3.0.txt) | `GPL-3.0-only` | FFmpeg / ffprobe 9.0.1 (Gyan `essentials_build`, which is GPL-enabled) | shipped: `release/win-unpacked/resources/backend/_internal/third-party/ffmpeg/LICENSE` | 35,147 |
| [`LGPL-2.1.txt`](LGPL-2.1.txt) | `LGPL-2.1-only` | UniDic dictionary data (the LGPL option of its triple licence) | fetched: <https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt> | 26,419 |
| [`PSF-2.0.txt`](PSF-2.0.txt) | `PSF-2.0` | typing_extensions 4.16.0 (`License-Expression: PSF-2.0`); aiohappyeyeballs 2.7.1 (`License: PSF-2.0`, classifier *Python Software Foundation License*) | shipped: `release/win-unpacked/resources/backend/_internal/typing_extensions-4.16.0.dist-info/licenses/LICENSE` | 13,936 |
| [`Python-LICENSE.txt`](Python-LICENSE.txt) | `PSF-2.0` | CPython 3.11.15 frozen interpreter (`python311.dll`) | shipped: `release/win-unpacked/resources/backend/_internal/third-party/python/LICENSE.txt` | 24,925 |
| [`Unlicense.txt`](Unlicense.txt) | `Unlicense` | yt-dlp 2026.08.19 (`prototype/hls-companion/vendor/yt-dlp/yt-dlp.exe`) | fetched: <https://raw.githubusercontent.com/yt-dlp/yt-dlp/2026.08.19/LICENSE> (tag pinned to the shipped build) | 1,211 |
| [`UniDic-COPYING.txt`](UniDic-COPYING.txt) | `LicenseRef-UniDic-triple` | UniDic dictionary data (`unidic_lite/dicdir/`) — states that the data is offered under GPL, LGPL *or* BSD | shipped: `release/win-unpacked/resources/backend/_internal/unidic_lite/dicdir/COPYING` | 194 |
| [`OFL-1.1-fonts.txt`](OFL-1.1-fonts.txt) | `OFL-1.1` | Noto Sans SC, Archivo Black, JetBrains Mono (the webfonts vendored under `prototype/hls-companion/web-player/fonts/`) | fetched: `https://cdn.jsdelivr.net/npm/@fontsource/<pkg>@5.3.0/LICENSE` for `noto-sans-sc`, `archivo-black`, `jetbrains-mono` | 5,075 |
| [`MIT-LagLingo.txt`](MIT-LagLingo.txt) | `MIT` | LagLingo itself (this repository's own `LICENSE`, not a third-party component) | shipped: `LICENSE` at the repository root | 1,067 |
| [`MIT-Electron.txt`](MIT-Electron.txt) | `MIT` | Electron 44.2.0 (which also brings Chromium and Node.js) | shipped: `release/win-unpacked/LICENSE.electron.txt` | 1,096 |
| [`MIT-attrs.txt`](MIT-attrs.txt) | `MIT` | attrs 26.1.0 (`License-Expression: MIT`) | shipped: `release/win-unpacked/resources/backend/_internal/attrs-26.1.0.dist-info/licenses/LICENSE` | 1,109 |
| [`MIT-fugashi.txt`](MIT-fugashi.txt) | `MIT` | fugashi 1.5.2 Python packaging (`License-Expression: MIT AND BSD-3-Clause`) | shipped: `release/win-unpacked/resources/backend/_internal/fugashi-1.5.2.dist-info/licenses/LICENSE` | 1,076 |
| [`MIT-langcodes.txt`](MIT-langcodes.txt) | `MIT` | langcodes 3.5.1 (classifier *MIT License*) | shipped: `release/win-unpacked/resources/backend/_internal/langcodes-3.5.1.dist-info/licenses/LICENSE.txt` | 1,090 |
| [`MIT-llhttp.txt`](MIT-llhttp.txt) | `MIT` | llhttp (vendored inside aiohttp 3.14.3, whose metadata declares `License: Apache-2.0 AND MIT`) | shipped: `release/win-unpacked/resources/backend/_internal/aiohttp-3.14.3.dist-info/licenses/vendor/llhttp/LICENSE` | 1,069 |
| [`MIT-unidic-lite.txt`](MIT-unidic-lite.txt) | `MIT` | unidic-lite 1.0.8 Python packaging (classifier *MIT License*) | shipped: `release/win-unpacked/resources/backend/_internal/unidic_lite-1.0.8.dist-info/licenses/LICENSE` | 1,051 |
| [`MIT-libffi.txt`](MIT-libffi.txt) | `MIT` | libffi 3.4.4 (`libffi-8.dll`, pulled in by the frozen CPython 3.11 interpreter) | fetched: <https://raw.githubusercontent.com/libffi/libffi/v3.4.4/LICENSE> | 1,132 |
| [`MIT-regenerator-runtime.txt`](MIT-regenerator-runtime.txt) | `MIT` | regenerator-runtime (bundled inside `hls.min.js` 1.7.1) | fetched: <https://github.com/babel/babel/blob/main/packages/babel-helpers/LICENSE> | 1,189 |
| [`MIT-N_m3u8DL-RE.txt`](MIT-N_m3u8DL-RE.txt) | `MIT` | N_m3u8DL-RE 0.6.0 (`prototype/hls-companion/vendor/n-m3u8dl-re/N_m3u8DL-RE.exe`) | fetched: <https://raw.githubusercontent.com/nilaoda/N_m3u8DL-RE/df70f0b3da0c630bd413bf617e758051f6b64757/LICENSE> (commit pinned to the shipped binary's `ProductVersion`) | 1,064 |
| [`BSD-3-Clause-idna.txt`](BSD-3-Clause-idna.txt) | `BSD-3-Clause` | idna 3.19 (`License-Expression: BSD-3-Clause`) | shipped: `release/win-unpacked/resources/backend/_internal/idna-3.19.dist-info/licenses/LICENSE.md` | 1,541 |
| [`BSD-3-Clause-mecab.txt`](BSD-3-Clause-mecab.txt) | `BSD-3-Clause` | MeCab (native library inside fugashi 1.5.2) | shipped: `release/win-unpacked/resources/backend/_internal/fugashi-1.5.2.dist-info/licenses/LICENSE.mecab` | 1,602 |
| [`BSD-3-Clause-unidic.txt`](BSD-3-Clause-unidic.txt) | `BSD-3-Clause` | UniDic dictionary data (the BSD option of its triple licence) | shipped: `…/_internal/unidic_lite/dicdir/BSD` **and** `…/_internal/unidic_lite-1.0.8.dist-info/licenses/LICENSE.unidic` | 1,541 |

## Per-component notices

MIT, BSD-3-Clause and OFL-1.1 all embed the copyright line in the notice itself, so those licences do **not** get a single shared file: each component gets its own, named `MIT-<component>.txt`, `BSD-3-Clause-<component>.txt` or, for the fonts, one file carrying all three notices. Apache-2.0 is the exception — its text carries no project copyright, so one `Apache-2.0.txt` covers every Apache-2.0 component and the §4(d) attribution lives in `THIRD_PARTY_NOTICES.md` instead.

## Verification

Every file was checked for the first non-empty line and at least one distinctive phrase of the licence it claims to be — `Apache License` + `Version 2.0, January 2004`; `GNU GENERAL PUBLIC LICENSE` + `Version 2, June 1991` / `Version 3, 29 June 2007`; `SIL OPEN FONT LICENSE Version 1.1`; `Permission is hereby granted, free of charge`; `Redistribution and use`; and so on. All 22 files passed.

Line endings were normalised to LF so the directory is internally consistent. That is the only transformation applied; the character content is exactly the source's. Files whose shipped source used CRLF therefore have a smaller byte count here than on disk (for example `Python-LICENSE.txt`: 25,066 → 24,925 bytes).

## Deliberate exclusions

- **BSD-2-Clause, MPL-2.0, CC0-1.0** — no file is created. Nothing in this repository is under any of them, and a licence text with no component behind it is noise. (Chromium does ship a handful of CC0/MPL-licensed files; those are enumerated in the shipped `LICENSES.chromium.html`, which is the correct place for them.)
- **A bare `MIT.txt` / `BSD-3-Clause.txt`** — deliberately not created, because a shared file for those licences would drop the per-project copyright lines that *are* the notice.
- **`LICENSES.chromium.html`** — not copied; the 20 MB shipped file is itself the authoritative aggregate for Electron/Chromium and duplicating it here would serve no purpose.

## Known discrepancies

Points where the shipped artifact and the canonical upstream text do not agree. In each case the file above is the one named in the *Source* column, chosen deliberately — not an oversight.

1. **`unidic_lite/dicdir/GPL` and `dicdir/LGPL` are older FSF printings.** They differ from today's gnu.org texts in the FSF's own boilerplate, not in the licence terms: `59 Temple Place, Suite 330, Boston, MA 02111-1307 USA` instead of `<https://fsf.org/>`, the pre-1999 name `GNU Library General Public License` instead of `GNU Lesser General Public License`, and the sample signatories `Ty Coon` instead of `Moe Ghoul`. `GPL-2.0.txt` and `LGPL-2.1.txt` here are the current canonical FSF texts; UniDic's terms are identical either way.
2. **`third-party/ffmpeg/LICENSE` uses `http://` where gnu.org now uses `https://`.** Four URLs, no change in terms. The shipped copy is kept because it is what the user has.
3. **`regenerator-runtime`'s notice points somewhere other than `facebook/regenerator`.** Line 2 of the shipped `hls.min.js` is:

   ```
   /*! regenerator-runtime -- Copyright (c) 2014-present, Facebook, Inc. -- license (MIT): https://github.com/babel/babel/blob/main/packages/babel-helpers/LICENSE */
   ```

   `MIT-regenerator-runtime.txt` therefore reproduces the document at *that* URL — the one the shipped notice itself names. It carries two copyright lines: `Copyright (c) 2014-present Sebastian McKenzie and other contributors` and `Copyright (c) 2014-present, Facebook, Inc. (ONLY ./src/helpers/regenerator* files)`. The alternative source <https://github.com/facebook/regenerator> has a different, narrower notice (`Copyright (c) 2014-present, Facebook, Inc.` only) and would drop the Babel line, so it was not used.
4. **`libffi-8.dll` carries no version resource**, so the exact bundled libffi release cannot be read off the binary. It was inferred: `python311.dll` is 3.11.15, and the CPython 3.11 branch's `PCbuild/get_externals.bat` pins `libffi-3.4.4`. Using `master` instead would have put `1996-2026` in the copyright line, which does not match this binary.

## Gaps this audit found (not fixed here)

Reported rather than silently patched, because fixing them means editing files outside `licenses/`:

- `THIRD_PARTY_NOTICES.md` never names **OpenSSL**, **libffi**, **N_m3u8DL-RE**, **mutagen** or **llhttp**, all of which are redistributed. `npm run guard:licences:frozen` fails on OpenSSL and libffi for exactly this reason (`NATIVE_COMPONENTS` in `scripts/check-licence-coverage.js` requires them to be named).
- `N_m3u8DL-RE.exe` is a tracked vendored binary with no licence file beside it, and it is not in the `COMPONENTS` list of `scripts/check-licence-coverage.js`, so nothing checks it.
- `hls.js` v1.7.1 ships an attribution/derivation notice beyond the Apache-2.0 text (Dailymotion copyright, plus `src/remux/mp4-generator.js` and `src/demux/exp-golomb.ts` derived from videojs-contrib-hls, © 2013-2015 Brightcove). That notice is not reproduced anywhere in this repository. Per the brief it belongs in `THIRD_PARTY_NOTICES.md`, not here: <https://raw.githubusercontent.com/video-dev/hls.js/v1.7.1/LICENSE>.
- The **Unicode licence / CLDR** question is unresolved. `THIRD_PARTY_NOTICES.md` says the checked-in `companion/data/languages.json` is generated from `langcodes` + `language_data` (MIT) and that 'CLDR data is used under the Unicode License'. No Unicode licence text was written here because it could not be established which Unicode licence version applies, or whether any CLDR-derived expression is still present in the generated artifact once it has been reduced to BCP 47 tags and language names. `langcodes` itself ships the MIT text reproduced as `MIT-langcodes.txt`.
- `multidict`'s shipped `LICENSE` is only the Apache-2.0 boilerplate with a copyright line, not the full licence text; `propcache` and `yarl` each ship a separate `NOTICE` with the aio-libs attribution. Those are §4(d) notices and, per the brief, live in `THIRD_PARTY_NOTICES.md`. `Apache-2.0.txt` supplies the terms.
