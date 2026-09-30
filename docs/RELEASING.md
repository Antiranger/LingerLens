# Release checklist

Build targets are Windows x64, macOS Intel x64 and macOS Apple Silicon arm64. Each backend is frozen on its own OS and CPU architecture. The release workflow creates a draft in the source repository; repository visibility is a separate owner action.

## Before a release candidate

- Confirm the version in `package.json`, the intended tag and the changelog entry.
- Review `git status`, tracked paths, current history and the complete diff. Remove credentials, Cookies, signed URLs, captures, runtime data, build output and stale experiments.
- Run `npm run ci`, `npm run desktop:test`, `npm run guard:licences:frozen` after a fresh backend build, and `git diff --check`.
- Run GitHub secret scanning and a history scan. A local clean tree is not proof that an old commit never contained a secret; rotate any exposed credential before publishing.
- Recheck `THIRD_PARTY_NOTICES.md`, `licenses/`, bundled binary checksums and corresponding-source obligations. The per-user installer disables `packElevateHelper` and does not redistribute `elevate.exe`.

## Windows candidate

```powershell
npm run desktop:refresh
npm run desktop:test
npm run desktop:dist
npm run guard:licences:frozen
$env:LINGERLENS_SMOKE_OUTPUT="$PWD/output/desktop-smoke"
& .\release\win-unpacked\LingerLens.exe --smoke-test
```

Test a clean Windows install, first launch, upgrade from the previous version, uninstall data retention, fullscreen, sleep/resume, network interruption, Cookie import and a representative stream. Record each result; installer existence or `--version` is not acceptance.

## GitHub workflow

The manually dispatched **Desktop packages** workflow builds all three platforms without publishing. It verifies the frozen backend with development tools removed from PATH, including FFmpeg, ffprobe, yt-dlp, provider imports and Japanese tokenization. An after-pack audit rejects private runtime paths and unexpected application files. The packaged application then starts with a fresh profile and plays synthetic video; screenshots and acceptance results are uploaded as smoke artifacts. These checks do not replace manual installation or real-provider acceptance.

After a verified revision is merged to `main`, push its `v<version>` tag. **Prepare desktop release** checks that the tag matches `package.json`, waits for all three packages and creates a draft containing the Windows EXE, both Mac DMGs and ZIPs, `SHA256SUMS.txt`, and the Windows updater's `manifest.json`. Review the assets and [.github/RELEASE_NOTES.md](../.github/RELEASE_NOTES.md) before publishing. A failed platform must not be described as supported by that release.

## macOS acceptance

See [native build instructions](../desktop/README.md). Test each architecture on a Mac: drag the app to Applications, launch without Python/Node/FFmpeg installed, configure providers, play video with sound and subtitles, toggle fullscreen, interrupt the network, sleep/resume and close the app. Install a new DMG to test replacement while preserving user data. Fixture tests do not prove real-provider quality, billing or platform access.

Preview packages are unsigned and macOS packages are not notarized. Developer ID signing and Apple notarization require maintainer credentials and a separately tested configuration. Do not disable Gatekeeper globally. Mac updates use a new DMG; the in-app EXE installer updater is Windows-only.

## Verify a download

Windows: `Get-FileHash .\LingerLens-0.1.2-windows-x64-setup.exe -Algorithm SHA256`.

Mac: `shasum -a 256 LingerLens-0.1.2-macos-arm64.dmg`.

Replace `0.1.2` and the architecture with the version and package you downloaded.

Compare the result with the matching line in the release's `SHA256SUMS.txt`. Checksums detect changed downloads; they do not replace publisher signatures. Preserve corresponding source and build scripts for redistributed components.
