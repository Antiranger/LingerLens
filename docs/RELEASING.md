# Release checklist

The source repository remains private until the owner explicitly changes that setting. This checklist prepares a reviewable release; it does not publish one.

## Before a release candidate

- Confirm the version in `package.json`, the intended tag and the changelog entry.
- Review `git status`, tracked paths, current history and the complete diff. Remove credentials, Cookies, signed URLs, captures, runtime data, build output and stale experiments.
- Run `npm run ci`, `npm run desktop:test`, `npm run guard:licences:frozen` after a fresh backend build, and `git diff --check`.
- Run GitHub secret scanning and a history scan. A local clean tree is not proof that an old commit never contained a secret; rotate any exposed credential before publishing.
- Recheck `THIRD_PARTY_NOTICES.md`, `licenses/`, bundled binary checksums and corresponding-source obligations. Resolve the flagged `elevate.exe` licence before redistributing an installer.

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

The release workflow builds on a version tag and creates a **draft** in the separate `Antiranger/LingerLens-releases` repository. It must not be changed to publish automatically. Review the installer, manifest, checksum, notices, source offer and unsigned-build warning before publishing manually. Keep the source repository private while doing this preparation.

macOS is not a release target yet: it needs a native backend/FFmpeg build, arm64/x64 CI, signing/notarization and clean-install tests. Android needs a separate client architecture.
