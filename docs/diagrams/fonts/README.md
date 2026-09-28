# Vendored diagram fonts

These are the exact three faces the diagram set uses, vendored so that
`docs/diagrams/*.html` render **offline and with zero third-party requests**.
Before this directory existed, every diagram pulled Google Fonts from
`https://fonts.googleapis.com` and `https://fonts.gstatic.com`, which broke both
the project's stated local-first/privacy posture and its own
[`no-remote-CDN rule`](../../../tests/test_no_remote_cdn.js). See
[issue #44](https://github.com/matthewhand/funes-vision/issues/44).

Faces are declared once in [`../fonts.css`](../fonts.css), which every diagram
links with a plain relative `<link rel="stylesheet" href="fonts.css">`. Adding
or re-skinning a face means editing that one file, never the 14 HTML files.

## Files

| File | Family | Style / weight | Bytes | Upstream version | Source |
|------|--------|----------------|-------|------------------|--------|
| `Geist-Variable.woff2` | Geist | normal, `wght` 100–900 (variable) | 69,760 | 1.800 | `vercel/geist-font` → `fonts/Geist/webfonts/Geist[wght].woff2` |
| `GeistMono-Variable.woff2` | Geist Mono | normal, `wght` 100–900 (variable) | 71,596 | 1.700 | `vercel/geist-font` → `fonts/GeistMono/webfonts/GeistMono[wght].woff2` |
| `InstrumentSerif-Regular.woff2` | Instrument Serif | normal, 400 | 27,316 | 1.000 | `google/fonts` → `ofl/instrumentserif/InstrumentSerif-Regular.ttf` |
| `InstrumentSerif-Italic.woff2` | Instrument Serif | italic, 400 | 28,012 | 1.000 | `google/fonts` → `ofl/instrumentserif/InstrumentSerif-Italic.ttf` |

All four cover 1000 upem and are byte-for-byte the upstream binaries except for
the woff2 container, so outlines, metrics and hinting match what the Google
Fonts CDN was serving.

`sha256` (first 16 hex chars) of the committed files:

```
Geist-Variable.woff2          2ffebe993e969069
GeistMono-Variable.woff2      afaacc4c5fbba89d
InstrumentSerif-Regular.woff2 f8d31fee94c2bc0d
InstrumentSerif-Italic.woff2  63cc2fcdc278dcc3
```

### How Instrument Serif was produced

`google/fonts` ships Instrument Serif as TTF only, so the two committed
`.woff2` files are a lossless container conversion of those originals
(`fontTools.ttLib`, `flavor = "woff2"`). No glyphs were subsetted, added,
removed, hinted or renamed, so the rendered result is identical to the CDN's
while staying a faithful copy of the upstream font. Re-derive with:

```bash
pip install fonttools brotli
python3 - <<'PY'
from fontTools.ttLib import TTFont
for n in ("InstrumentSerif-Regular", "InstrumentSerif-Italic"):
    f = TTFont(f"{n}.ttf"); f.flavor = "woff2"; f.save(f"{n}.woff2")
PY
```

Geist and Geist Mono are the upstream **webfonts**, copied verbatim — Vercel
already publishes the same variable binaries it serves, so no conversion was
needed.

## Licensing — SIL Open Font License 1.1

Both families are OFL 1.1, which grants use, bundling, embedding and
redistribution provided the copyright notice and license travel with the files.
The full license text is committed here:

- [`OFL-InstrumentSerif.txt`](OFL-InstrumentSerif.txt) — Copyright 2022 The
  Instrument Serif Project Authors (<https://github.com/Instrument/instrument-serif>).
  Covers `InstrumentSerif-{Regular,Italic}.woff2`.
- [`OFL-Geist.txt`](OFL-Geist.txt) — Copyright 2024 The Geist Project Authors
  (<https://vercel/geist-font>). Covers `Geist-Variable.woff2` and
  `GeistMono-Variable.woff2`.

Neither upstream copyright statement declares a **Reserved Font Name**, so OFL
§3's rename restriction does not apply and the files keep their original family
names.

Neither file is renamed or subsetted here, and no font is sold or used to imply
endorsement by the authors, so OFL §2 and §4 are satisfied as-is. Because these
are now redistributed inside a repository, a copy of the OFL text and this
notice must stay with the `docs/diagrams/fonts/` directory — **do not delete
`OFL-*.txt`**.

## Updating a vendored face

1. Fetch the new binary from the upstream OFL release listed above, and its
   `OFL.txt` if the upstream license file changed.
2. Update the byte count, version and `sha256` in the tables above.
3. Confirm the file is still licensed OFL before replacing anything.
4. `bash tests/run.sh` and the `self_check.py` loop in
   [`../README.md`](../README.md) must stay green.

## Why not a system font stack

A system-stack fallback (the option considered first) would have made the CDN
request disappear, but it changes the documented typography: the display face
would stop being Instrument Serif on every machine, so headings would render in
whatever serif the reader happens to have and the 13 diagrams would stop
looking like a set. Self-hosting keeps the design intact and is a one-time
~192 KB. See the `Risks / follow-ups` section of the PR that introduced this
directory.
