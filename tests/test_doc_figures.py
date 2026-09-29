"""The hard figures in this repo's prose must not rot.

Six successive cleanup PRs (#90, #92-#99, #101-#104) were spent finding stale
numbers that a human had written down and nothing had checked. The failures
were always the same shape -- a figure that *disagreed* with the thing it
described, or a name that no longer existed:

  * ``index.html``'s own header comment claimed 95 ``pure:NAME`` pairs while
    the file carried 96;
  * 11 of the 16 rows in the ``docs/diagrams/README.md`` index table pointed at
    code that had moved, three of them at blank lines;
  * ``DEVELOP.md`` documented ``canonicalLabel()`` (deleted in a10d237) and
    claimed "11 routes" where ``api_server.py`` dispatches 15;
  * the diagrams said "three vendored faces" where ``fonts.css`` declares four.

Five guards already cover everything *except* these numbers
(``tests/test_section_map.js`` for the section map, ``tests/test_no_remote_cdn.js``
for remote hosts / fonts / diagram floor, ``tests/test_static_asset_manifest.js``,
``tests/test_guide_img_budget.py`` and ``tests/test_demo_build.py``). This file
is the sixth.

Design -- invariants, not volatile absolutes
---------------------------------------------
Line numbers and byte counts churn on every ordinary edit, and a guard that
churns is a guard somebody deletes. So the assertions here are:

* **set-equality** between a documented name-list and the real set (the
  "Covered today" sentinel list, the index-table rows, the documented API
  routes, the vendored faces across three documents). This is the high-value
  one: it catches additions, removals, renames, and a function name pasted in
  where a marker name belonged -- which is exactly what happened in
  ``DEVELOP.md``;
* **relationship** checks, so two figures that describe one thing cannot
  contradict each other -- the ``<style>`` block's documented line range and its
  documented ``~90 KB`` must describe the same slice, and the per-face byte
  counts in two documents must both be the file's real size;
* **existence** checks for every file, name, id and route a document names;
* a **floor** on each derived set, so a parser that silently stops matching
  fails loudly instead of passing on an empty set.

Deliberately NOT asserted, and why
----------------------------------
* the ``~90 rules`` figure in ``tests/helpers/load.cjs`` -- two independent
  reviews found it definition-dependent (522 rules via ``cssRules()``, 440
  unique selectors, 102 with a why-comment above) and motivational rather than
  certifying. It stays fuzzy.
* absolute line numbers in a citation (``analyze_images.py:1008``). Only the
  *existence of the file* and the *sanity of the range* are checked, because a
  citation that names a line which no longer exists is wrong in a way a reader
  notices, while a citation that names a line which moved is wrong only until
  the next commit.
* the Python-file and "689 passed" counts in diagram 15, and anything else a
  contributor's own change to ``tests/`` would invalidate. Adding or removing a
  suite is not a documentation bug.
* anything asserted only from a source comment (a Python module comment, a CSS
  comment) unless the same number is also claimed in prose that has to be
  re-derived anyway.

Known, deliberately uncovered drift
----------------------------------
``docs/diagrams/15-test-ci-gates.html`` line 91, the machine-readable
``<desc>``, still says "the 94 Node suites" while two visible labels in the
same diagram say 97. That is a real staleness, but fixing it means editing a
diagram this file does not own, so it is reported rather than guarded. The
``41 files`` / ``689 passed`` counts on line 144 of the same diagram are
correct today and will be off by one the moment this file is added; they are
not asserted for that reason.

Run:  python3 -m pytest tests/test_doc_figures.py -q
"""
import glob
import hashlib
import re
import unittest
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVELOP = ROOT / "DEVELOP.md"
INDEX = ROOT / "index.html"
API_SERVER = ROOT / "api_server.py"
DIAGRAMS = ROOT / "docs" / "diagrams"
FONTS_CSS = DIAGRAMS / "fonts.css"
FONTS_DIR = DIAGRAMS / "fonts"
FONTS_README = FONTS_DIR / "README.md"
DIAGRAM_README = DIAGRAMS / "README.md"
CONVENTIONS = DIAGRAMS / "_conventions.md"
TESTS = ROOT / "tests"

# Floors. Every derived set is checked against one of these so that a parser
# which quietly stops matching fails instead of reporting success on nothing.
MIN_SENTINELS = 90        # 96 `// === pure:NAME ===` markers today
MIN_DIAGRAMS = 16         # 16 numbered diagrams (+ _template.html = 17 files)
MIN_FACES = 4             # 4 vendored woff2 faces
MIN_INDEX_ROWS = 16       # 16 rows in the diagram index table
MIN_ROUTES = 15           # 15 API paths dispatched by api_server.py
MIN_PATH_CLAIMS = 40      # distinct path-shaped tokens across the four documents
MIN_NAME_ANNOTATIONS = 6  # `(apply_retention)`-style name annotations in footers


@cache
def _read(path):
    """Read a text file once per process; several assertions want the same one."""
    return str(Path(path).read_text(encoding="utf-8"))


def _label(path):
    """Repo-relative path, for failure messages. `fonts/README.md` and
    `docs/diagrams/README.md` share a basename, so `.name` would be ambiguous."""
    return str(Path(path).relative_to(ROOT))


# ---------------------------------------------------------------------------
# Parsers. Each one is a named helper so a failure message can point at it.
# ---------------------------------------------------------------------------

SENTINEL_OPEN = re.compile(r"// === pure:([A-Za-z_][A-Za-z0-9_]*) ===")
BACKTICKED_IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")

COVERED_TODAY = re.compile(
    r"Covered\s+today\b[^\n]*?all (\d+) sentinel pairs, in file order:"
    r"(.*?)(?=\n\s*-\s\*\*)",
    re.S,
)


def documented_sentinels():
    """(claimed count, [names]) from DEVELOP.md's "Covered today" list.

    Re-derive the real list with::

        grep -oE '// === pure:[A-Za-z_][A-Za-z0-9_]* ===' index.html \\
          | sed -E 's|.*pure:([A-Za-z_][A-Za-z0-9_]*) .*|\\1|'
    """
    m = COVERED_TODAY.search(_read(DEVELOP))
    if m is None:
        raise AssertionError(
            "DEVELOP.md no longer contains the sentence this test anchors on: "
            "'Covered today - all <N> sentinel pairs, in file order:' followed by "
            "the backticked name list and then a '- **' bullet. If the wording "
            "changed, re-derive the list from index.html (see the grep above) "
            "and update COVERED_TODAY in tests/test_doc_figures.py."
        )
    return int(m.group(1)), BACKTICKED_IDENT.findall(m.group(2))


def sentinel_markers():
    """The `// === pure:NAME ===` open markers in index.html, in file order.

    Same regex as tests/test_sentinel_inventory.py, on purpose: if one of them
    has to change, both guards change together.
    """
    return SENTINEL_OPEN.findall(_read(INDEX))


API_PATH = re.compile(r'self\.path\s*==\s*"/(api/[^"]+)"')
ROUTE_SENTENCE = re.compile(r"exposes (\d+) routes")
ROUTE_LIST_END = re.compile(r"`events`\s+SSE")


def dispatched_routes():
    """The distinct `self.path == "/api/..."` values api_server.py dispatches.

    Re-derive with::

        grep -oE 'self\\.path == "/api/[^"]+"' api_server.py | sort -u
    """
    return sorted(set(API_PATH.findall(_read(API_SERVER))))


def documented_routes():
    """(claimed count, [leaf names]) from DEVELOP.md's api_server.py paragraph.

    The sentence is cut at "`events` SSE" because everything backticked after
    it (`image.new`, `new-burst`, `X-Accel-Buffering: no`) is an SSE event name
    or a header, not a route.
    """
    para = _read(DEVELOP)
    start = para.index("exposes ")
    para = para[start:para.index("\n\n", start)]
    count = ROUTE_SENTENCE.match(para)
    end = ROUTE_LIST_END.search(para)
    if count is None or end is None:
        raise AssertionError(
            "DEVELOP.md's api_server.py paragraph no longer reads 'exposes <N> "
            "routes - ... and the `events` SSE stream'. Re-derive the real route "
            "list with the grep in dispatched_routes() and update ROUTE_SENTENCE "
            "/ ROUTE_LIST_END in tests/test_doc_figures.py."
        )
    names = []
    for token in re.findall(r"`([^`]+)`", para[:end.end()]):
        token = re.sub(r"^(?:GET|POST)\s+", "", token)   # `POST /api/clip`
        token = token.lstrip("/")                       # `/api/clip` -> `api/clip`
        names.append(re.sub(r"^api/", "", token))       # -> `clip`
    return int(count.group(1)), names


def diagram_files():
    """([numbered names], [all names]) for docs/diagrams/*.html, sorted.

    Re-derive with::

        ls docs/diagrams/*.html | wc -l
    """
    names = sorted(p.name for p in DIAGRAMS.glob("*.html"))
    return [n for n in names if re.match(r"\d\d-", n)], names


# `file.ext` optionally preceded by path segments. The extension is deliberately
# loose so `settings.example.json` is captured whole rather than as
# `.example.json`; the negative lookbehind keeps `..` and bare suffixes out.
CITATION = re.compile(
    r"(?<![\w/])"
    r"(?P<file>(?:[.A-Za-z0-9_][\w.@-]*/)*[.A-Za-z0-9_][\w.-]*\.[A-Za-z][A-Za-z0-9]*)"
    r"(?P<ref>(?::[0-9][0-9]*(?:[-,+][0-9]*)*(?:\s*,\s*[0-9]+(?:[-,+][0-9]*)*)*)*)"
)
PARENTHETICAL = re.compile(r"\(([^()]{1,40})\)")
FOOTER = re.compile(r'<div class="footer">(.*?)</div>', re.S)
ASSUMPTION = re.compile(r"\b(?:No )?[Aa]ssumption")

# Identifiers that appear in a citation's parentheses but describe the *kind* of
# thing cited rather than naming it. `(routes)` and `(style)` and `(sentinels)`
# are English; they are excluded from the name check on purpose, because
# `api_server.py` contains no word "routes" and asserting it would be asserting
# a description, not a name.
PROSE_ANNOTATIONS = frozenset({"routes", "style", "sentinels"})


def footer_text(diagram):
    """The `Source:` footer of a diagram, tags stripped, assumptions removed.

    Everything from the first "Assumption" onwards is narrative, not citation.
    """
    m = FOOTER.search(_read(diagram))
    if m is None:
        return None
    body = re.sub(r"<[^>]+>", " ", m.group(1)).replace("&nbsp;", " ")
    body = ASSUMPTION.split(body)[0]
    return re.sub(r"\s+", " ", body).replace("·", " ").strip()


def footer_citations(body):
    """[(file, ref)] in citation order. `ref` is the raw `:12,34-56` string.

    Matches inside a parenthetical are skipped: diagram 10 writes
    `create-index.sh:22-28 (images.json)`, and `images.json` is a note about
    what the range covers, not a file the footer points at.
    """
    notes = [m.span() for m in PARENTHETICAL.finditer(body)]
    out = []
    for m in CITATION.finditer(body):
        if any(start <= m.start() < end for start, end in notes):
            continue
        out.append((m.group("file"), m.group("ref")))
    return out


def footer_annotations(body):
    """[(note, file)] for every parenthesised note in a footer.

    A note belongs to the citation immediately before it, so the file whose
    `file` group is in scope is tracked as the body is walked left to right.
    A bare `:1276-1295` with no filename continues the previous file, which is
    how diagram 10 cites three ranges inside analyze_images.py.
    """
    events = sorted(
        [(m.start(), "file", m.group("file")) for m in CITATION.finditer(body)]
        + [(m.start(), "note", m.group(1)) for m in PARENTHETICAL.finditer(body)]
    )
    out, current = [], None
    for _, kind, value in events:
        if kind == "file":
            current = value
        else:
            out.append((value, current))
    return out


def cited_line_numbers(ref):
    """Every line number in a citation ref, as ints. `''` -> []."""
    return [int(n) for n in re.findall(r"[0-9]+", ref or "")]


INDEX_ROW = re.compile(
    r"^\| \[`([\w.-]+)`\]\(([\w.-]+)\)\s*\|(?P<rest>.*)$", re.M
)

# Extensions that make a backticked token a claim about a file in *this* repo.
REPO_EXTENSIONS = (
    ".py", ".sh", ".js", ".mjs", ".cjs", ".md", ".html", ".css", ".conf",
    ".json", ".yml", ".yaml", ".txt", ".cron", ".service", ".timer", ".woff2",
)

# Tokens that look like a path but are not a claim about a file in this repo.
# Each entry is a *recorded* exception, and
# `test_the_external_path_allowlist_has_not_gone_stale` fails if one of them
# becomes resolvable -- the same self-invalidating escape hatch
# tests/test_static_asset_manifest.js uses for KNOWN_GAPS.
KNOWN_EXTERNAL_PATHS = frozenset({
    # The `diagram-design` skill's linter and reference doc. They live in the
    # contributor's ~/.claude/skills/diagram-design/, never in this repo.
    "self_check.py",
    "SKILL.md",
    # Upstream font repositories and their internal paths, quoted verbatim in
    # docs/diagrams/fonts/README.md as provenance.
    "vercel/geist-font",
    "google/fonts",
    "fonts/Geist/webfonts/Geist[wght].woff2",
    "fonts/GeistMono/webfonts/GeistMono[wght].woff2",
    "ofl/instrumentserif/InstrumentSerif-Regular.ttf",
    "ofl/instrumentserif/InstrumentSerif-Italic.ttf",
    "OFL.txt",
    ".woff2",
    # Pipeline-written, .gitignore'd runtime data. Never committed.
    "images.json",
    "pins.json",
    # URL paths, not filesystem paths.
    "/api/health",
    "/css2",
    # A bare-filename shorthand in _conventions.md for tests/test_no_remote_cdn.js.
    "test_no_remote_cdn.js",
})


def _expand_braces(token):
    """`a-{x,y}.woff2` -> [`a-x.woff2`, `a-y.woff2`]."""
    m = re.search(r"\{([^}]*)\}", token)
    if m is None:
        return [token]
    return [
        token[:m.start()] + alt + token[m.end():]
        for alt in m.group(1).split(",")
    ]


def _is_path_claim(token):
    """Is this backticked/link token a claim about a file path?"""
    if not token or any(c.isspace() for c in token):
        return False
    if "://" in token or token.startswith(("~", "#", "<")) or token.endswith(">"):
        return False
    if re.fullmatch(r"rgba?\(|[\d.,%\s/()\-]+", token):   # colours, "4,3", ":21"
        return False
    return "/" in token.rstrip("*") or token.endswith(REPO_EXTENSIONS)


def _path_candidates(token, doc):
    """Every path a document-relative token could mean, most specific first."""
    doc = Path(doc)
    return [ROOT / token, doc.parent / token, doc.parent.parent / token]


def _resolves(token, doc):
    """True if `token` names something that exists, relative to `doc`.

    A glob (`fonts/OFL-*.txt`) is satisfied by a match inside the document's own
    directory tree as well as by a literal hit, because the diagram prose says
    "`OFL-*.txt` files" without repeating the `fonts/` prefix.
    """
    for concrete in _expand_braces(token):
        for cand in _path_candidates(concrete, doc):
            if "*" in concrete:
                if glob.glob(str(cand)) or list(Path(doc).parent.rglob(concrete)):
                    return True
            elif cand.exists():
                return True
    return False


def path_claims(doc):
    """Sorted distinct path-shaped tokens in a document."""
    text = _read(doc)
    tokens = [m.group(1) for m in re.finditer(r"`([^`\n]+)`", text)]
    tokens += [m.group(1) for m in re.finditer(r"\]\(([^)\s]+)\)", text)]
    return sorted({t for t in tokens if _is_path_claim(t)})


# ---------------------------------------------------------------------------
# DEVELOP.md -- the "Covered today" sentinel list
# ---------------------------------------------------------------------------

class TestCoveredTodaySentinelList(unittest.TestCase):
    """DEVELOP.md's pure-helper list must be the set index.html actually wraps.

    This is the check that would have caught both historical failures: a
    sentinel added without a docs mention, a sentinel removed without one, and
    a function name (not a marker name) pasted into the list.
    """

    def test_the_list_is_set_equal_to_the_sentinels_in_index_html(self):
        _, documented = documented_sentinels()
        markers = sentinel_markers()
        self.assertGreaterEqual(
            len(markers), MIN_SENTINELS,
            "only %d `// === pure:NAME ===` markers found in index.html, expected "
            "at least %d -- the marker syntax or the file layout moved; re-derive "
            "with: grep -c '// === pure:' index.html"
            % (len(markers), MIN_SENTINELS),
        )
        only_doc = sorted(set(documented) - set(markers))
        only_code = sorted(set(markers) - set(documented))
        self.assertEqual(
            (only_doc, only_code), ([], []),
            "DEVELOP.md's 'Covered today' sentinel list and index.html's "
            "`// === pure:NAME ===` markers are different sets.\n"
            "  in DEVELOP.md but not wrapped in index.html: %s\n"
            "  wrapped in index.html but missing from DEVELOP.md: %s\n"
            "Re-derive the real list with:\n"
            "  grep -oE '// === pure:[A-Za-z_][A-Za-z0-9_]* ===' index.html \\\n"
            "    | sed -E 's|.*pure:([A-Za-z_][A-Za-z0-9_]*) .*|\\1|'\n"
            "and edit DEVELOP.md -> Testing -> 'Node suites cover' (the list must "
            "stay in file order). Do not relax the assertion here."
            % (only_doc, only_code),
        )

    def test_no_name_appears_twice_in_the_list(self):
        _, documented = documented_sentinels()
        dupes = sorted({n for n in documented if documented.count(n) > 1})
        self.assertEqual(
            dupes, [],
            "DEVELOP.md's 'Covered today' list repeats %s; it is a set, so a "
            "repeated name means a copy/paste slip rather than a second "
            "sentinel. Re-derive the real list with the grep in "
            "documented_sentinels()." % (dupes,),
        )

    def test_the_stated_count_is_the_number_of_markers(self):
        claimed, documented = documented_sentinels()
        markers = sentinel_markers()
        self.assertEqual(
            claimed, len(markers),
            "DEVELOP.md says 'all %d sentinel pairs' but index.html carries %d "
            "`// === pure:NAME ===` markers. Re-derive with: "
            "grep -c '// === pure:' index.html" % (claimed, len(markers)),
        )
        self.assertEqual(
            len(documented), len(markers),
            "DEVELOP.md's 'Covered today' list names %d sentinels but the "
            "sentence claims %d and index.html has %d markers. Re-derive with: "
            "grep -c '// === pure:' index.html"
            % (len(documented), claimed, len(markers)),
        )

    def test_the_diagram_14_and_15_sentinel_claims_agree(self):
        markers = len(sentinel_markers())
        claims = {
            DIAGRAMS / "14-frontend-internals.html":
                [r"the (\d+) sentinel-wrapped pure helpers",
                 r">(\d+) pure:NAME sentinel pairs<",
                 r"— (\d+) pairs today\.<"],
            DIAGRAMS / "15-test-ci-gates.html":
                [r"asserted here: (\d+) pure: sentinels"],
        }
        for doc, patterns in claims.items():
            for pattern in patterns:
                with self.subTest(doc=_label(doc), claim=pattern):
                    m = re.search(pattern, _read(doc))
                    self.assertIsNotNone(
                        m, "%s no longer contains the sentence this test "
                           "anchors on (%s); re-derive the real count with: "
                           "grep -c '// === pure:' index.html" % (_label(doc), pattern),
                    )
                    self.assertEqual(
                        int(m.group(1)), markers,
                        "%s claims %s sentinel pairs, index.html has %d. "
                        "Re-derive with: grep -c '// === pure:' index.html"
                        % (_label(doc), m.group(1), markers),
                    )


# ---------------------------------------------------------------------------
# DEVELOP.md -- the API route count
# ---------------------------------------------------------------------------

class TestDocumentedApiRoutes(unittest.TestCase):
    """'exposes 15 routes' must match what api_server.py dispatches."""

    def test_the_route_count_matches_the_dispatch_sites(self):
        claimed, documented = documented_routes()
        routes = dispatched_routes()
        self.assertGreaterEqual(
            len(routes), MIN_ROUTES,
            "only %d distinct `self.path == \"/api/...\"` values found in "
            "api_server.py, expected at least %d -- the dispatch style moved; "
            "re-derive with: grep -oE 'self\\.path == \"/api/[^\"]+\"' "
            "api_server.py | sort -u" % (len(routes), MIN_ROUTES),
        )
        self.assertEqual(
            claimed, len(routes),
            "DEVELOP.md's api_server.py paragraph says 'exposes %d routes' but "
            "api_server.py dispatches %d distinct paths. Re-derive with:\n"
            "  grep -oE 'self\\.path == \"/api/[^\"]+\"' api_server.py | sort -u"
            % (claimed, len(routes)),
        )
        self.assertEqual(
            len(documented), len(routes),
            "DEVELOP.md enumerates %d route names but claims %d and api_server.py "
            "dispatches %d. Re-derive with:\n"
            "  grep -oE 'self\\.path == \"/api/[^\"]+\"' api_server.py | sort -u"
            % (len(documented), claimed, len(routes)),
        )

    def test_every_documented_route_is_dispatched(self):
        _, documented = documented_routes()
        routes = dispatched_routes()
        leaves = {p.rsplit("/", 1)[-1] for p in routes}
        self.assertEqual(
            len(leaves), len(routes),
            "two dispatched API paths share a final segment, so the documented "
            "route list can no longer be compared to them by name. Disambiguate "
            "the full paths in DEVELOP.md and teach this test to read them; "
            "re-derive with: grep -oE 'self\\.path == \"/api/[^\"]+\"' "
            "api_server.py | sort -u",
        )
        self.assertEqual(
            sorted(documented), sorted(leaves),
            "DEVELOP.md's route list and api_server.py's dispatch sites are "
            "different sets.\n"
            "  documented but not dispatched: %s\n"
            "  dispatched but not documented: %s\n"
            "Re-derive the real list with:\n"
            "  grep -oE 'self\\.path == \"/api/[^\"]+\"' api_server.py | sort -u\n"
            "A leaf of `/api/a/b` is written as `b` in the document."
            % (sorted(set(documented) - leaves), sorted(leaves - set(documented))),
        )


# ---------------------------------------------------------------------------
# docs/diagrams/README.md -- the index table
# ---------------------------------------------------------------------------

class TestDiagramIndexTable(unittest.TestCase):
    """The 16-row index table must describe the 16 diagrams that exist."""

    def setUp(self):
        self.rows = INDEX_ROW.findall(_read(DIAGRAM_README))
        self.numbered, self.all_html = diagram_files()

    def test_the_table_has_one_row_per_diagram_on_disk(self):
        self.assertGreaterEqual(
            len(self.numbered), MIN_DIAGRAMS,
            "only %d numbered diagrams under docs/diagrams/, expected at least "
            "%d. Re-derive with: ls docs/diagrams/[0-9][0-9]-*.html | wc -l"
            % (len(self.numbered), MIN_DIAGRAMS),
        )
        # Set-equality first: it is the actionable message ("which diagram is
        # missing from which side"). The row-count floor then explains *why* the
        # set came out short, if the table's Markdown shape changed at all.
        linked = sorted(href for _, href, _ in self.rows)
        self.assertEqual(
            linked, sorted(self.numbered),
            "the docs/diagrams/README.md index table and docs/diagrams/*.html do "
            "not list the same diagrams.\n"
            "  in the table but not on disk: %s\n"
            "  on disk but not in the table: %s\n"
            "Re-derive the real row list with: "
            "ls docs/diagrams/[0-9][0-9]-*.html"
            % (sorted(set(linked) - set(self.numbered)),
               sorted(set(self.numbered) - set(linked))),
        )
        self.assertGreaterEqual(
            len(self.rows), MIN_INDEX_ROWS,
            "only %d rows parsed out of the docs/diagrams/README.md index table, "
            "expected at least %d -- the table's Markdown shape changed. "
            "Re-derive the real row list with: "
            "grep -c '^| \\[`' docs/diagrams/README.md" % (len(self.rows), MIN_INDEX_ROWS),
        )

    def test_every_row_links_a_file_that_exists(self):
        for name, href, _ in self.rows:
            with self.subTest(row=name, href=href):
                self.assertEqual(
                    name, href,
                    "docs/diagrams/README.md row links [%s](%s) -- the label and "
                    "the target disagree" % (name, href),
                )
                self.assertTrue(
                    (DIAGRAMS / href).is_file(),
                    "docs/diagrams/README.md links %s, which is not a file under "
                    "docs/diagrams/. Re-derive the real list with: "
                    "ls docs/diagrams/*.html" % href,
                )

    def test_every_file_cited_by_a_row_exists(self):
        for name, _, rest in self.rows:
            sources = rest.rsplit("|", 1)[0]      # drop the trailing empty column
            for cited, ref in footer_citations(sources):
                with self.subTest(row=name, cited=cited):
                    path = ROOT / cited
                    self.assertTrue(
                        path.is_file(),
                        "the index-table row for %s cites %s, which is not in the "
                        "repo. Re-derive with: ls %s" % (name, cited, cited),
                    )
                    lines = len(_read(path).splitlines()) if path.is_file() else 0
                    for num in cited_line_numbers(ref):
                        self.assertLessEqual(
                            num, lines,
                            "the index-table row for %s cites %s:%s but that file "
                            "has only %d lines -- the citation is stale. Re-derive "
                            "the cited ranges with: grep -n '<symbol>' %s"
                            % (name, cited, num, lines, cited),
                        )


# ---------------------------------------------------------------------------
# The per-diagram `Source:` footers
# ---------------------------------------------------------------------------

class TestDiagramFooters(unittest.TestCase):
    """Every `file:line` citation in a footer must still name a real file."""

    def _diagrams(self):
        return [DIAGRAMS / n for n in diagram_files()[1]]

    def test_the_template_footer_is_still_a_placeholder(self):
        body = footer_text(DIAGRAMS / "_template.html")
        self.assertIsNotNone(body, "_template.html has no `Source:` footer")
        self.assertIn(
            "file:line refs", body,
            "_template.html's footer is no longer the `[file:line refs]` "
            "placeholder this test skips; give it real citations and add "
            "_template.html to the guarded set, or restore the placeholder",
        )

    def test_every_file_cited_in_a_footer_exists(self):
        cited = 0
        for diagram in self._diagrams():
            body = footer_text(diagram)
            self.assertIsNotNone(body, "%s has no `Source:` footer" % diagram.name)
            for path, ref in footer_citations(body):
                with self.subTest(diagram=diagram.name, cited=path):
                    cited += 1
                    target = ROOT / path
                    self.assertTrue(
                        target.is_file(),
                        "%s's footer cites %s, which is not in the repo. "
                        "Re-derive the cited files with:\n"
                        "  grep -oE '[A-Za-z0-9_./-]+\\.[a-z]+' "
                        "docs/diagrams/%s | sort -u"
                        % (diagram.name, path, diagram.name),
                    )
                    lines = len(_read(target).splitlines())
                    for num in cited_line_numbers(ref):
                        self.assertLessEqual(
                            num, lines,
                            "%s's footer cites %s:%s but that file has only %d "
                            "lines -- the citation is stale. Re-derive with: "
                            "grep -n '<symbol>' %s"
                            % (diagram.name, path, num, lines, path),
                        )
        self.assertGreaterEqual(
            cited, 40,
            "only %d file citations were parsed out of the 16 diagram footers; the "
            "citation pattern must have stopped matching. Re-derive with: "
            "grep -oE '[A-Za-z0-9_./-]+\\.[a-z]+' docs/diagrams/*.html | wc -l"
            % cited,
        )

    def test_every_name_annotated_in_a_footer_exists_in_its_file(self):
        checked = 0
        for diagram in self._diagrams():
            body = footer_text(diagram)
            for note, path in footer_annotations(body):
                if not path or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", note):
                    continue          # prose, or a `key=value` / `name.json` note
                if note in PROSE_ANNOTATIONS:
                    continue
                with self.subTest(diagram=diagram.name, note=note, file=path):
                    checked += 1
                    target = ROOT / path
                    self.assertTrue(
                        target.is_file(),
                        "%s's footer annotates (%s) against %s, which is not in "
                        "the repo" % (diagram.name, note, path),
                    )
                    self.assertRegex(
                        _read(target), r"(?i)\b%s\b" % re.escape(note),
                        "%s's footer names %s as living in %s, but that identifier "
                        "does not occur in %s -- it was renamed or the citation is "
                        "attributed to the wrong file. Re-derive with:\n"
                        "  grep -n '\\b%s\\b' %s"
                        % (diagram.name, note, path, path, note, path),
                    )
        self.assertGreaterEqual(
            checked, MIN_NAME_ANNOTATIONS,
            "only %d identifier-shaped footer annotations were found, expected at "
            "least %d. The annotation pattern must have stopped matching. "
            "Re-derive with: "
            "grep -oE '\\([^)]+\\)' docs/diagrams/*.html" % (checked, MIN_NAME_ANNOTATIONS),
        )

    def test_the_systemd_timer_annotation_matches_the_unit(self):
        body = footer_text(DIAGRAMS / "06-sequence-recovery.html")
        m = re.search(r"(\S+\.timer):([0-9]+)\s*\((\w+)=([^)]+)\)", body)
        self.assertIsNotNone(
            m, "06-sequence-recovery.html no longer cites a `.timer` unit with a "
               "`Key=Value` note; re-derive with: "
               "grep -n 'OnUnitActiveSec' systemd/webcam-healthcheck.timer",
        )
        unit, _line, key, value = m.groups()
        self.assertTrue(
            (ROOT / unit).is_file(),
            "06-sequence-recovery.html cites %s, which is not in the repo" % unit,
        )
        self.assertIn(
            "%s=%s" % (key, value), _read(ROOT / unit).splitlines()[int(m.group(2)) - 1],
            "06-sequence-recovery.html cites %s:%s (%s=%s) but line %s of that "
            "unit does not set it. Re-derive with: grep -n '%s' %s"
            % (unit, m.group(2), key, value, m.group(2), key, unit),
        )


# ---------------------------------------------------------------------------
# Path existence across the four diagram documents
# ---------------------------------------------------------------------------

DIAGRAM_DOCS = (DIAGRAM_README, CONVENTIONS, FONTS_README, FONTS_CSS)


class TestDiagramDocPaths(unittest.TestCase):
    """Every path a diagram document names has to exist (or be a recorded
    out-of-repo reference)."""

    def test_every_path_claim_resolves(self):
        total = 0
        for doc in DIAGRAM_DOCS:
            for token in path_claims(doc):
                with self.subTest(doc=_label(doc), path=token):
                    total += 1
                    if token in KNOWN_EXTERNAL_PATHS:
                        continue
                    self.assertTrue(
                        _resolves(token, doc),
                        "%s names `%s`, which does not exist in this repo. "
                        "Re-derive with:\n"
                        "  ls -d %s        # against the repo root\n"
                        "  ls -d %s  # against the document's own directory\n"
                        "(if this is genuinely an out-of-repo reference, add it to "
                        "KNOWN_EXTERNAL_PATHS in tests/test_doc_figures.py with a "
                        "reason)"
                        % (_label(doc), token, token,
                           _label(doc.parent / token)),
                    )
        self.assertGreaterEqual(
            total, MIN_PATH_CLAIMS,
            "only %d path-shaped tokens were parsed out of the four diagram "
            "documents, expected at least %d -- the token pattern must have "
            "stopped matching. Re-derive with: "
            "grep -ohE '`[^`]+`' docs/diagrams/README.md "
            "docs/diagrams/_conventions.md docs/diagrams/fonts/README.md "
            "docs/diagrams/fonts.css | sort -u | wc -l" % (total, MIN_PATH_CLAIMS),
        )

    def test_the_external_path_allowlist_has_not_gone_stale(self):
        """A recorded out-of-repo reference that has since landed in the repo is
        a stale exception, exactly like a recorded KNOWN_GAPS asset."""
        for doc in DIAGRAM_DOCS:
            claimed = set(path_claims(doc))
            for token in sorted(KNOWN_EXTERNAL_PATHS & claimed):
                with self.subTest(doc=_label(doc), path=token):
                    self.assertFalse(
                        _resolves(token, doc),
                        "%s lists `%s` as an out-of-repo reference in "
                        "KNOWN_EXTERNAL_PATHS, but that path now exists -- drop "
                        "it from the allowlist so it is checked like every other "
                        "path" % (_label(doc), token),
                    )

    def test_the_profile_marker_the_index_table_names_exists(self):
        marker = ROOT / ".diagram-design"
        self.assertTrue(
            marker.is_file(),
            "docs/diagrams/README.md says the profile is selected by the "
            "`.diagram-design` marker at the repo root, but that file is gone. "
            "Re-derive with: ls -a .diagram-design",
        )
        self.assertRegex(
            _read(marker), r"(?m)^profile:\s*funes-vision\s*$",
            ".diagram-design must still carry `profile: funes-vision`; the "
            "diagram set is skinned from that. Re-derive with: cat .diagram-design",
        )


# ---------------------------------------------------------------------------
# Diagram / face counts
# ---------------------------------------------------------------------------

def face_files():
    """The vendored woff2 filenames, sorted. `ls docs/diagrams/fonts/*.woff2`."""
    return sorted(p.name for p in FONTS_DIR.glob("*.woff2"))


def declared_faces():
    """The woff2 files fonts.css points at, via @font-face src url()."""
    return sorted(set(re.findall(r"url\('fonts/([\w.-]+\.woff2)'\)", _read(FONTS_CSS))))


def declared_families():
    return sorted(set(re.findall(r"font-family: '([^']+)'", _read(FONTS_CSS))))


NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
# One capturing group, so it drops straight into a surrounding pattern.
NUM = r"(\d+|" + "|".join(NUMBER_WORDS) + r")"


def _number(token):
    """'16' -> 16, 'four' -> 4."""
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


# (document, pattern, expected value getter, what the number is)
#
# The diagrams spell small counts out ("as the four woff2 faces below"), so the
# patterns accept a digit or a number word and _number() reads both.
COUNT_CLAIMS = (
    (DIAGRAM_README, r"That is \*\*%s diagrams\*\*" % NUM,
     lambda c: c["numbered"], "numbered diagrams on disk"),
    (DIAGRAM_README, r"the %s diagrams would stop\s+looking like a set" % NUM,
     lambda c: c["numbered"], "numbered diagrams on disk"),
    (DIAGRAM_README, r"as the %s woff2 faces below" % NUM,
     lambda c: c["faces"], "vendored woff2 faces in docs/diagrams/fonts/"),
    (CONVENTIONS, r"the %s vendored woff2 faces" % NUM,
     lambda c: c["faces"], "vendored woff2 faces in docs/diagrams/fonts/"),
    (CONVENTIONS, r"not the %s HTML files" % NUM,
     lambda c: c["html"], "HTML files under docs/diagrams/ (16 + _template.html)"),
    (FONTS_README, r"never the %s HTML files" % NUM,
     lambda c: c["html"], "HTML files under docs/diagrams/ (16 + _template.html)"),
    (FONTS_README, r"the %s diagrams would stop" % NUM,
     lambda c: c["numbered"], "numbered diagrams on disk"),
    (FONTS_README, r"the exact %s faces" % NUM,
     lambda c: c["families"], "distinct font-family in fonts.css"),
    (FONTS_CSS, r"all %s diagrams plus _template\.html" % NUM,
     lambda c: c["numbered"], "numbered diagrams on disk"),
)


class TestDiagramAndFaceCounts(unittest.TestCase):
    """The diagram and face counts, stated in five places, must agree."""

    def test_every_count_claim_matches_what_is_on_disk(self):
        numbered, all_html = diagram_files()
        counts = {
            "numbered": len(numbered),
            "html": len(all_html),
            "faces": len(face_files()),
            "families": len(declared_families()),
        }
        self.assertGreaterEqual(
            counts["numbered"], MIN_DIAGRAMS,
            "only %d numbered diagrams under docs/diagrams/, expected at least "
            "%d. Re-derive with: ls docs/diagrams/[0-9][0-9]-*.html | wc -l"
            % (counts["numbered"], MIN_DIAGRAMS),
        )
        self.assertGreaterEqual(
            counts["faces"], MIN_FACES,
            "only %d vendored woff2 faces in docs/diagrams/fonts/, expected at "
            "least %d. Re-derive with: ls docs/diagrams/fonts/*.woff2"
            % (counts["faces"], MIN_FACES),
        )
        for doc, pattern, derive, what in COUNT_CLAIMS:
            with self.subTest(doc=_label(doc), claim=pattern):
                m = re.search(pattern, _read(doc))
                self.assertIsNotNone(
                    m, "%s no longer contains the sentence this test anchors on "
                       "(%s); re-derive the real count with the command in the "
                       "message for the matching claim" % (_label(doc), pattern),
                )
                self.assertEqual(
                    _number(m.group(1)), derive(counts),
                    "%s says '%s', but there are %d (%s). Re-derive with: "
                    "ls docs/diagrams/ | wc -l"
                    % (_label(doc), m.group(1), derive(counts), what),
                )

    def test_the_face_set_is_equal_across_disk_fonts_css_and_both_readmes(self):
        disk = face_files()
        tree = sorted(set(re.findall(
            r"[│├└─\s]+([\w.-]+\.woff2)\s+#", _read(DIAGRAM_README))))
        table = sorted(set(re.findall(
            r"^\| `([\w.-]+\.woff2)` \|", _read(FONTS_README), re.M)))
        for label, claimed in (("docs/diagrams/README.md tree", tree),
                               ("docs/diagrams/fonts/README.md table", table),
                               ("docs/diagrams/fonts.css @font-face src", declared_faces())):
            with self.subTest(source=label):
                self.assertGreaterEqual(
                    len(disk), MIN_FACES,
                    "only %d woff2 faces in docs/diagrams/fonts/, expected at "
                    "least %d. Re-derive with: ls docs/diagrams/fonts/*.woff2"
                    % (len(disk), MIN_FACES),
                )
                self.assertEqual(
                    claimed, disk,
                    "the vendored face list in %s does not match "
                    "docs/diagrams/fonts/*.woff2.\n"
                    "  claimed but not on disk: %s\n"
                    "  on disk but not claimed: %s\n"
                    "Re-derive with: ls docs/diagrams/fonts/*.woff2"
                    % (label, sorted(set(claimed) - set(disk)),
                       sorted(set(disk) - set(claimed))),
                )


# ---------------------------------------------------------------------------
# Font byte counts and checksums
# ---------------------------------------------------------------------------

class TestFontFigures(unittest.TestCase):
    """The byte counts and checksums both font documents record must be real."""

    def test_the_fonts_readme_byte_column_matches_the_files(self):
        rows = re.findall(
            r"^\| `([\w.-]+\.woff2)` \|[^|]*\|[^|]*\| ([\d,]+) \|",
            _read(FONTS_README), re.M)
        self.assertGreaterEqual(
            len(rows), MIN_FACES,
            "only %d rows parsed from the docs/diagrams/fonts/README.md face "
            "table, expected at least %d. Re-derive with: "
            "grep -cE '^\\| \\`[\\w.-]+\\.woff2\\`' docs/diagrams/fonts/README.md"
            % (len(rows), MIN_FACES),
        )
        for name, claimed in rows:
            with self.subTest(face=name):
                path = FONTS_DIR / name
                self.assertTrue(path.is_file(), "docs/diagrams/fonts/%s is gone" % name)
                self.assertEqual(
                    int(claimed.replace(",", "")), path.stat().st_size,
                    "docs/diagrams/fonts/README.md records %s as %s bytes; the "
                    "file is %d. Re-derive with: stat -c %%s "
                    "docs/diagrams/fonts/%s" % (name, claimed, path.stat().st_size, name),
                )

    def test_the_diagram_readme_tree_byte_annotations_match_the_files(self):
        rows = re.findall(
            r"[│├└─\s]+([\w.-]+\.woff2)\s+#\s+([\d,]+) B",
            _read(DIAGRAM_README))
        self.assertGreaterEqual(
            len(rows), MIN_FACES,
            "only %d byte annotations parsed from the docs/diagrams/README.md "
            "file tree, expected at least %d -- the tree block's shape changed. "
            "Re-derive with: grep -oE '[\\w.-]+\\.woff2 +# +[0-9,]+ B' "
            "docs/diagrams/README.md" % (len(rows), MIN_FACES),
        )
        for name, claimed in rows:
            with self.subTest(face=name):
                path = FONTS_DIR / name
                self.assertTrue(path.is_file(), "docs/diagrams/fonts/%s is gone" % name)
                self.assertEqual(
                    int(claimed.replace(",", "")), path.stat().st_size,
                    "docs/diagrams/README.md annotates %s as %s B; the file is %d. "
                    "Re-derive with: stat -c %%s docs/diagrams/fonts/%s"
                    % (name, claimed, path.stat().st_size, name),
                )

    def test_the_recorded_sha256_prefixes_match_the_files(self):
        block = re.findall(
            r"^(\S+\.woff2)\s+([0-9a-f]{16})\s*$", _read(FONTS_README), re.M)
        self.assertGreaterEqual(
            len(block), MIN_FACES,
            "only %d sha256 lines parsed from docs/diagrams/fonts/README.md, "
            "expected at least %d. Re-derive with: "
            "grep -cE '^[\\w.-]+\\.woff2 +[0-9a-f]{16}$' "
            "docs/diagrams/fonts/README.md" % (len(block), MIN_FACES),
        )
        for name, claimed in block:
            with self.subTest(face=name):
                path = FONTS_DIR / name
                self.assertTrue(path.is_file(), "docs/diagrams/fonts/%s is gone" % name)
                real = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
                self.assertEqual(
                    claimed, real,
                    "docs/diagrams/fonts/README.md records sha256 %s... for %s; "
                    "the committed file hashes to %s... Re-derive with: "
                    "sha256sum docs/diagrams/fonts/%s | cut -c1-16"
                    % (claimed, name, real, name),
                )

    def test_the_self_hosting_size_claim_matches_the_vendored_total(self):
        faces = face_files()
        self.assertGreaterEqual(
            len(faces), MIN_FACES,
            "only %d woff2 faces, expected at least %d -- the total below would "
            "be meaningless. Re-derive with: ls docs/diagrams/fonts/*.woff2"
            % (len(faces), MIN_FACES),
        )
        total = sum((FONTS_DIR / f).stat().st_size for f in faces)
        for doc in (DIAGRAM_README, FONTS_README):
            with self.subTest(doc=_label(doc)):
                m = re.search(r"one-time\s*~(\d+) KB", _read(doc))
                self.assertIsNotNone(
                    m, "%s no longer states the one-time self-hosting size; "
                       "re-derive the real total with: "
                       "du -cb docs/diagrams/fonts/*.woff2 | tail -1" % _label(doc),
                )
                claimed_kib = int(m.group(1))
                # The prose reads the total in KiB (196,684 B = 192.07 KiB), so
                # 2% is the honest tolerance for a "~" figure.
                real_kib = total / 1024
                self.assertAlmostEqual(
                    real_kib, claimed_kib, delta=claimed_kib * 0.02,
                    msg="%s calls the vendored set 'a one-time ~%d KB'; the %d "
                        "faces on disk total %d bytes = %.1f KiB. Re-derive with: "
                        "du -cb docs/diagrams/fonts/*.woff2 | tail -1"
                        % (_label(doc), claimed_kib, len(faces), total, real_kib),
                )


# ---------------------------------------------------------------------------
# Diagram 14 -- one figure, two numbers, one slice
# ---------------------------------------------------------------------------

class TestStyleBlockClaim(unittest.TestCase):
    """Diagram 14's "the ~90 KB <style> block (lines 141-2746)" is two claims
    about one thing, so the two can be checked against each other."""

    CARD = re.compile(
        r"The ~([\d,]+) KB <code>&lt;style&gt;</code> block "
        r"\(lines (\d+)[–-](\d+)\)")
    FOOTER_RANGE = re.compile(r"index\.html:.*?\b(\d+)[–-](\d+) \(style\)")

    def _style_span(self):
        """The (first, last) 1-based line numbers of index.html's <style> block."""
        lines = _read(INDEX).splitlines(keepends=True)
        first = next((i for i, ln in enumerate(lines, 1) if "<style>" in ln), None)
        last = next((i for i, ln in enumerate(lines, 1) if "</style>" in ln), None)
        return first, last, lines

    def test_the_documented_range_is_the_style_block_and_the_size_agrees(self):
        first, last, lines = self._style_span()
        self.assertIsNotNone(
            first, "index.html has no `<style>` line; re-derive with: "
                   "grep -n '</\\?style>' index.html",
        )
        self.assertIsNotNone(
            last, "index.html has no `</style>` line; re-derive with: "
                  "grep -n '</\\?style>' index.html",
        )
        card = self.CARD.search(_read(DIAGRAMS / "14-frontend-internals.html"))
        self.assertIsNotNone(
            card, "14-frontend-internals.html no longer states the ~N KB <style> "
                  "block line range; re-derive with: "
                  "grep -n 'style></code> block' docs/diagrams/"
                  "14-frontend-internals.html",
        )
        claimed_kb = int(card.group(1).replace(",", ""))
        claimed_span = (int(card.group(2)), int(card.group(3)))
        self.assertEqual(
            claimed_span, (first, last),
            "14-frontend-internals.html says the <style> block is lines %d-%d, but "
            "index.html's <style> runs %d-%d. Re-derive both with:\n"
            "  grep -n '</\\?style>' index.html"
            % (claimed_span[0], claimed_span[1], first, last),
        )
        measured = len("".join(lines[first - 1:last]).encode("utf-8"))
        # "~90 KB" is a rounded figure, so allow 5% before calling it a
        # contradiction; at 5% a 5 KB CSS addition is still tolerated.
        self.assertAlmostEqual(
            measured, claimed_kb * 1000, delta=claimed_kb * 1000 * 0.05,
            msg="14-frontend-internals.html calls the <style> block at lines %d-%d "
                "'~%d KB'; those %d lines are %d bytes. One of the two numbers is "
                "wrong. Re-derive with:\n"
                "  sed -n '%d,%dp' index.html | wc -c"
                % (first, last, claimed_kb, last - first + 1, measured, first, last),
        )

    def test_the_card_and_the_footer_cite_the_same_style_range(self):
        card = self.CARD.search(_read(DIAGRAMS / "14-frontend-internals.html"))
        self.assertIsNotNone(card, "14-frontend-internals.html has no <style> card")
        foot = self.FOOTER_RANGE.search(footer_text(DIAGRAMS / "14-frontend-internals.html"))
        self.assertIsNotNone(
            foot, "14-frontend-internals.html's footer no longer cites index.html "
                  "with a `(style)` range; re-derive with: "
                  "grep -n 'style)' docs/diagrams/14-frontend-internals.html",
        )
        self.assertEqual(
            (int(card.group(2)), int(card.group(3))),
            (int(foot.group(1)), int(foot.group(2))),
            "14-frontend-internals.html cites the <style> block as lines %s-%s in "
            "its card and %s-%s in its footer -- the same slice, two numbers. "
            "Re-derive with: grep -n '</\\?style>' index.html"
            % (card.group(2), card.group(3), foot.group(1), foot.group(2)),
        )


# ---------------------------------------------------------------------------
# Diagram 15 -- the suite counts
# ---------------------------------------------------------------------------

class TestDiagram15SuiteCounts(unittest.TestCase):
    """The counts in 15-test-ci-gates.html that are exactly derivable.

    Not asserted: "41 files" / "689 passed" (a contributor adding or removing a
    tests/ file invalidates them without any documentation being wrong), the
    "<desc>"'s "94 Node suites" (already stale, and fixing it means editing a
    diagram this test does not own), and "1,349 lines" for a11y_audit.js (a line
    count churns on every edit to that file).
    """

    def test_the_node_suite_count_claims_match_the_suites_on_disk(self):
        suites = sorted(p.name for p in TESTS.glob("*.js"))
        self.assertGreaterEqual(
            len(suites), 50,
            "only %d tests/*.js suites found, expected at least 50; re-derive "
            "with: ls tests/*.js | wc -l" % len(suites),
        )
        body = _read(DIAGRAMS / "15-test-ci-gates.html")
        claims = (
            (r">(\d+) Node suites —", (1,)),
            (r"bash tests/run\.sh · (\d+)/(\d+) ·", (1, 2)),
            (r"(\d+) files is a list", (1,)),
        )
        for pattern, groups in claims:
            with self.subTest(claim=pattern):
                m = re.search(pattern, body)
                self.assertIsNotNone(
                    m, "15-test-ci-gates.html no longer contains %s; re-derive "
                       "the real count with: ls tests/*.js | wc -l" % pattern,
                )
                for group in groups:
                    self.assertEqual(
                        int(m.group(group)), len(suites),
                        "15-test-ci-gates.html claims %s Node suites; tests/ has "
                        "%d. Re-derive with: ls tests/*.js | wc -l (and the "
                        "runner's own count, printed by: bash tests/run.sh)"
                        % (m.group(group), len(suites)),
                    )

    def test_the_loader_backed_suite_count_matches(self):
        body = _read(DIAGRAMS / "15-test-ci-gates.html")
        m = re.search(r"(\d+) suites read index\.html through tests/helpers/load\.cjs", body)
        self.assertIsNotNone(
            m, "15-test-ci-gates.html no longer states how many suites read "
               "index.html through the loader; re-derive with: "
               "grep -lE \"require\\('\\./helpers/load\\.cjs'\\)\" tests/*.js | wc -l",
        )
        using_loader = [
            p.name for p in sorted(TESTS.glob("*.js"))
            if re.search(r"require\(['\"]\./helpers/load\.cjs['\"]\)", _read(p))
        ]
        self.assertEqual(
            int(m.group(1)), len(using_loader),
            "15-test-ci-gates.html says %s suites read index.html through "
            "tests/helpers/load.cjs; %d do. Re-derive with:\n"
            "  grep -lE \"require\\('\\./helpers/load\\.cjs'\\)\" tests/*.js | wc -l"
            % (m.group(1), len(using_loader)),
        )


# ---------------------------------------------------------------------------
# DEVELOP.md -- the environment-override defaults table
# ---------------------------------------------------------------------------

ENV_DEFAULT = re.compile(
    r'(?:_env_int|os\.environ\.get)\(\s*"(WEBCAM_[A-Z_]+)"\s*,\s*(?P<val>[^,)]+?)\s*[,)]')
CONST_ASSIGN = re.compile(r"(?m)^\s*([A-Z][A-Z0-9_]*) = (.+?)\s*(?:#.*)?$")
INT_EXPR = re.compile(r"[0-9 *+]+")
STR_LITERAL = re.compile(r'^"([^"]*)"$')


def _default_for(name, source):
    """The default api_server.py passes for WEBCAM_<name>, or None.

    api_server.py writes its defaults three ways -- an int literal, an
    expression over a module constant (``1024 * 1024``), a reference to a class
    or module constant (``self.MAX_BODY_BYTES``) and a string constant
    (``DEFAULT_CORS_ORIGIN``). All four are resolved here, with a bounded
    evaluator: a name is looked up in the constant assignments and a literal is
    either digits-and-arithmetic or a quoted string. Nothing is exec'd from
    outside that grammar.
    """
    seen = set()
    for m in ENV_DEFAULT.finditer(source):
        if m.group(1) != name:
            continue
        val = m.group("val")
        while True:
            if INT_EXPR.fullmatch(val):
                return int(eval(val, {"__builtins__": {}}, {}))    # digits, * +
            lit = STR_LITERAL.match(val)
            if lit:
                return lit.group(1)
            ref = re.fullmatch(r"(?:self\.|Handler\.)?([A-Z][A-Z0-9_]*)", val)
            if ref is None or ref.group(1) in seen:
                return None
            seen.add(ref.group(1))
            consts = dict(CONST_ASSIGN.findall(source))
            if ref.group(1) not in consts:
                return None
            val = consts[ref.group(1)]
    return None


class TestEnvDefaultTable(unittest.TestCase):
    """DEVELOP.md's env table says its defaults are "cross-checked against
    api_server.py". Check the ones api_server.py states explicitly.

    Two rows are skipped by design. `WEBCAM_API_TOKEN` and `WEBCAM_TZ` are read
    with no fallback in the code, so their cells describe a fallback chain
    ("unset (auth off)", "settings.json `timezone` > `Australia/Sydney`") rather
    than a figure -- that is prose, and a guard over it would be asserting an
    interpretation.
    """

    TABLE_HEAD = re.compile(r"^\| Env \| Default \| Read \| Meaning \|\s*$", re.M)
    ENV_ROW = re.compile(r"^\| `(WEBCAM_[A-Z_]+)` \|([^|]*)\|", re.M)

    def _env_table(self):
        text = _read(DEVELOP)
        head = self.TABLE_HEAD.search(text)
        if head is None:
            raise AssertionError(
                "DEVELOP.md no longer has the '| Env | Default | Read | Meaning |' "
                "table; re-derive the real defaults with: "
                "grep -n '_env_int\\|os.environ.get' api_server.py"
            )
        table = text[head.end():head.end() + 4000]
        return table[:table.index("\n\n")]

    def test_every_stated_default_matches_api_server(self):
        api = _read(API_SERVER)
        rows = self.ENV_ROW.findall(self._env_table())
        self.assertGreaterEqual(
            len(rows), 11,
            "only %d rows parsed from DEVELOP.md's env-override table, expected at "
            "least 11 -- the table's Markdown shape changed. Re-derive with: "
            "grep -cE '^\\| \\`WEBCAM_' DEVELOP.md" % len(rows),
        )
        derived = 0
        for name, cell in rows:
            with self.subTest(env=name):
                want = _default_for(name, api)
                if want is None:
                    continue        # no explicit default in the code
                derived += 1
                claimed = re.search(r"`([^`]+)`", cell)
                self.assertIsNotNone(
                    claimed,
                    "DEVELOP.md's row for %s states no backticked default (%r); "
                    "api_server.py defaults it to %s. Re-derive with: "
                    "grep -n '%s' api_server.py" % (name, cell.strip(), want, name),
                )
                self.assertEqual(
                    claimed.group(1), str(want),
                    "DEVELOP.md's env table gives %s a default of %s; "
                    "api_server.py uses %s. Re-derive with: grep -n '%s' "
                    "api_server.py" % (name, claimed.group(1), want, name),
                )
        self.assertGreaterEqual(
            derived, 9,
            "only %d of the %d env rows had an explicit default in api_server.py; "
            "the default-extraction pattern must have stopped matching. "
            "Re-derive with: grep -n '_env_int\\|os.environ.get' api_server.py"
            % (derived, len(rows)),
        )


if __name__ == "__main__":
    unittest.main()
