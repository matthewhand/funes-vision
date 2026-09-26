# Contributing to funes-vision

Thanks for wanting to help. This is a focused two-camera homelab project —
small, targeted pull requests are the easiest to review and merge.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Dev setup

Prerequisites and the full install path live in [docs/INSTALL.md](docs/INSTALL.md).
For day-to-day development:

```bash
git clone https://github.com/matthewhand/funes-vision.git
cd funes-vision
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp settings.example.json settings.json   # edit watch_dirs / yolo_dir / timezone
```

The gallery is a single self-contained file (`index.html`); the backend is
stdlib Python plus the four imports pinned in `requirements.txt`. No build step
is required for normal changes.

## Tests

Both suites must pass before you open a PR:

```bash
python3 -m unittest discover -s tests   # backend + pure helpers (stdlib unittest)
bash tests/run.sh                       # Node assert suites, tests/*.js
```

CI (`.github/workflows/ci.yml`) runs the same two commands on Python 3.11 and
3.12 with Node 20. Two zone tests read the deployment-specific, gitignored
`settings.json`; in a clean checkout they skip rather than fail.

### Test sentinel contract (do not break this)

The SPA's pure logic is tested **without a headless browser**. Each function the
browser uses is wrapped in sentinel comments inside `index.html`:

```js
// === pure:functionName ===
function functionName(...) { ... }
// === /pure:functionName ===
```

The Node suites extract that block by regex and `eval` it, so there is exactly
one copy of the function — it both ships in the page and is unit-tested. If you
add or rename a pure helper, add matching `// === pure:NAME ===` /
`// === /pure:NAME ===` sentinels and a test that extracts it. Do not duplicate
the function outside its sentinels.

Tests live in `tests/`: `test_*.py` (Python) and `test_*.js` (Node). A new
behavior should come with a test in one of those forms. `tests/test_dom_refs.js`
guards that every `getElementById('x')` matches an `id="x"` in the markup.

## Pull request expectations

- Keep the change focused; unrelated refactors go in a separate PR.
- Run both test suites and paste the result in the PR body.
- Follow the existing style: standard library first, minimal dependencies, no
  new third-party runtime imports without discussion.
- Update docs (`README.md`, `docs/`, `DEVELOP.md`) when behavior or
  configuration changes.
- Fill in the [pull request template](.github/pull_request_template.md).
- Never commit secrets or live catalogs. `settings.json`, `integrations.json`,
  `analysis.json`, and the other state files at the repo root are gitignored —
  keep it that way. See [SECURITY.md](SECURITY.md).

## Reporting bugs and requesting features

Open an issue with the appropriate template under
[Issues](https://github.com/matthewhand/funes-vision/issues). For anything
security-sensitive, follow [SECURITY.md](SECURITY.md) instead of opening a
public issue.
