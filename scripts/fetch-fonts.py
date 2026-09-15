#!/usr/bin/env python3
"""Fetch and vendor the LingerLens UI webfonts.

The desktop web player is a strictly offline, same-origin app: `index.html` may
not reference any external `<script>` or `<link>` (asserted by
`prototype/hls-companion/tests/test_web_assets.js`), and the companion server
only serves assets from a fixed route allow-list. So the fonts the design calls
for have to live in the repository.

This script is the single source of truth for those binaries. It is a *build
tool*: run it only when the font set changes, then commit the result under
`prototype/hls-companion/web-player/fonts/` plus the generated
`prototype/hls-companion/web-player/fonts.css`.

Design notes
------------
* Noto Sans SC is delivered by @fontsource as ~101 unicode-range slices per
  weight. Slicing is kept on purpose: a browser then downloads only the slices a
  page actually needs (tens of KB) instead of a single multi-megabyte file. The
  four non-CJK subsets (cyrillic, vietnamese, latin-ext) are skipped because the
  UI never renders them and the system stack covers them.
* `latin` is kept for every family, otherwise Latin digits and punctuation inside
  a Chinese sentence would fall back to a different typeface.
* Versions are pinned to the exact resolved release so a rebuild is byte-stable.

Usage
-----
    python scripts/fetch-fonts.py            # fetch everything, rewrite fonts.css
    python scripts/fetch-fonts.py --check    # verify the vendored files match
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_PLAYER = REPO_ROOT / "prototype" / "hls-companion" / "web-player"
FONTS_DIR = WEB_PLAYER / "fonts"
FONTS_CSS = WEB_PLAYER / "fonts.css"

# Pinned @fontsource releases (all three ship from the same monorepo cadence).
VERSION = "5.3.0"
CDN = f"https://cdn.jsdelivr.net/npm/@fontsource/{{package}}@{VERSION}"

# Subsets worth vendoring. Everything else in the package is dropped.
# * `latin`         — keeps Latin digits/letters inside CJK runs on-brand.
# * bare numbers    — @fontsource's unicode-range CJK slices (see module docstring).
# Deliberately dropped: `latin-ext`, `cyrillic`, `cyrillic-ext`, `vietnamese`,
# `greek` — the UI never renders them and the system stack covers them.
def keep_subset(subset: str) -> bool:
    return subset == "latin" or subset.isdigit()

# CSS emitted per family. `family` is the CSS font-family name; `weights` are the
# weights we vendor; `package` is the @fontsource package name.
FAMILIES: tuple[dict, ...] = (
    {"family": "Archivo Black", "slug": "archivo-black", "package": "archivo-black", "weights": (400,)},
    {"family": "JetBrains Mono", "slug": "jetbrains-mono", "package": "jetbrains-mono", "weights": (500, 700, 800)},
    {"family": "Noto Sans SC", "slug": "noto-sans-sc", "package": "noto-sans-sc", "weights": (400, 500, 700, 900)},
)

FACE_RE = re.compile(r"@font-face\s*\{(?P<body>[^}]*)\}", re.DOTALL)
# @fontsource file names are `<package-slug>-<subset>-<weight>-normal.woff2`.
# Stripping the known slug first matters because subset names contain hyphens
# (`latin-ext`, `cyrillic-ext`) and the slug itself does too (`archivo-black`).
SUFFIX = "-normal"


def split_face_name(filename: str, slug: str) -> tuple[str, int]:
    stem = filename[: -len(".woff2")]
    if not stem.endswith(SUFFIX):
        raise SystemExit(f"unexpected @fontsource file name: {filename}")
    remainder = stem[: -len(SUFFIX)]
    prefix = f"{slug}-"
    if not remainder.startswith(prefix):
        raise SystemExit(f"unexpected @fontsource file name: {filename}")
    parts = remainder[len(prefix):].rsplit("-", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        raise SystemExit(f"unexpected @fontsource file name: {filename}")
    return parts[0], int(parts[1])


@dataclass(frozen=True)
class Face:
    family: str
    weight: int
    subset: str
    url: str
    unicode_range: str


def http_get(url: str, *, binary: bool) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "lingerlens-fetch-fonts/1"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - pinned https URLs
        payload = response.read()
    return payload if binary else payload


def parse_faces(css: str, family: str, weight: int, css_url: str, slug: str) -> list[Face]:
    faces: list[Face] = []
    for match in FACE_RE.finditer(css):
        body = match.group("body")
        src = re.search(r"url\(([^)]+?\.woff2)\)", body)
        urange = re.search(r"unicode-range:\s*([^;]+);", body)
        if not src or not urange:
            continue
        filename = src.group(1).rsplit("/", 1)[-1]
        subset, _found_weight = split_face_name(filename, slug)
        if not keep_subset(subset):
            continue
        faces.append(
            Face(
                family=family,
                weight=weight,
                subset=subset,
                url=f"{css_url.rsplit('/', 1)[0]}/{src.group(1).lstrip('./')}",
                unicode_range=" ".join(urange.group(1).split()),
            )
        )
    return faces


def collect() -> tuple[list[Face], dict[str, bytes]]:
    """Return (faces in emit order, {relative destination: bytes})."""
    faces: list[Face] = []
    payloads: dict[str, bytes] = {}
    for entry in FAMILIES:
        slug = entry["slug"]
        for weight in entry["weights"]:
            # Single-weight families ship only `index.css`; multi-weight families
            # ship one `<weight>.css` per weight.
            candidates = (f"{weight}.css", "index.css")
            css = None
            css_url = ""
            for candidate in candidates:
                css_url = f"{CDN.format(package=entry['package'])}/{candidate}"
                try:
                    css = http_get(css_url, binary=False).decode("utf-8")
                    break
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise SystemExit(f"cannot fetch {css_url}: {error}") from error
            if css is None:
                raise SystemExit(f"no stylesheet for {entry['package']} weight {weight} (tried {', '.join(candidates)})")
            found = parse_faces(css, entry["family"], weight, css_url, slug)
            if not found:
                raise SystemExit(f"no usable @font-face blocks in {css_url}")
            for face in found:
                destination = f"{slug}/{face.subset}-{weight}.woff2"
                if destination not in payloads:
                    payloads[destination] = http_get(face.url, binary=True)
                faces.append(face)
            print(f"  {entry['family']:<16} {weight:>3}  {len(found):>3} slices", flush=True)
    return faces, payloads


FAMILY_SLUG = {entry["family"]: entry["slug"] for entry in FAMILIES}


def render_css(faces: list[Face]) -> str:
    lines = [
        "/* LingerLens UI webfonts — GENERATED by scripts/fetch-fonts.py; do not edit by hand.",
        f"   Vendored from @fontsource {VERSION} (SIL Open Font License 1.1).",
        "   Same-origin on purpose: the player must run fully offline with no CDN. */",
        "",
    ]
    for face in faces:
        slug = FAMILY_SLUG[face.family]
        lines.append(
            "@font-face{"
            f'font-family:"{face.family}";font-style:normal;font-display:swap;'
            f"font-weight:{face.weight};"
            f"src:url(/fonts/{slug}/{face.subset}-{face.weight}.woff2) format('woff2');"
            f"unicode-range:{face.unicode_range}}}"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify vendored files instead of writing them")
    args = parser.parse_args(argv)

    if args.check:
        if not FONTS_CSS.is_file():
            print("FAIL fonts.css is missing; run scripts/fetch-fonts.py", file=sys.stderr)
            return 1
        referenced = re.findall(r"url\((/fonts/[^)]+)\)", FONTS_CSS.read_text(encoding="utf-8"))
        missing = [item for item in referenced if not (WEB_PLAYER / item.lstrip("/")).is_file()]
        if missing:
            print(f"FAIL {len(missing)} referenced font files are missing, e.g. {missing[0]}", file=sys.stderr)
            return 1
        vendored = list(FONTS_DIR.rglob("*.woff2"))
        total = sum(path.stat().st_size for path in vendored)
        print(f"OK {FONTS_CSS.name} references {len(referenced)} faces backed by {len(vendored)} files ({total / 1e6:.1f} MB)")
        return 0

    print(f"fetching @fontsource {VERSION} …")
    faces, payloads = collect()
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for relative, blob in payloads.items():
        target = FONTS_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.read_bytes() == blob:
            continue
        target.write_bytes(blob)
        written += 1
    FONTS_CSS.write_text(render_css(faces), encoding="utf-8", newline="\n")
    total = sum(path.stat().st_size for path in FONTS_DIR.rglob("*.woff2"))
    digest = hashlib.sha256(FONTS_CSS.read_bytes()).hexdigest()
    print(f"\n{len(payloads)} font files ({written} changed), {total / 1e6:.1f} MB total")
    print(f"{FONTS_CSS.relative_to(REPO_ROOT)}  sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
