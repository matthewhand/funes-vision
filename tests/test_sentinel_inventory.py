"""Sentinel-inventory guard for the SPA pure-helper tests (issue #55).

Every tests/*.js Node suite extracts a function from index.html between
``// === pure:NAME ===`` and ``// === /pure:NAME`` markers. If a rename drops
a marker, the owning test fails but nothing tells you the marker is gone
globally. This guard scans the test sources for every referenced ``pure:NAME``
and asserts the matching open/close markers still exist in index.html.

It also defends the markers themselves:

* no two sentinel blocks share a name (a copy/paste that would make the
  per-name regex in the .js suites grab the wrong block), and
* no sentinel block is empty (markers left behind after the body was deleted).

Run from the repo root:  python3 -m unittest discover -s tests
"""
import glob
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(REPO_ROOT, "index.html")
TEST_GLOB = os.path.join(REPO_ROOT, "tests", "*.js")

SENTINEL_REF = re.compile(r"pure:([A-Za-z_][A-Za-z0-9_]*)")
OPEN_MARKER = re.compile(r"// === pure:([A-Za-z_][A-Za-z0-9_]*) ===")
CLOSE_MARKER = re.compile(r"// === /pure:([A-Za-z_][A-Za-z0-9_]*) ===")


def _read_index_html():
    with open(INDEX_HTML, encoding="utf-8") as fh:
        return fh.read()


def _has_code(body):
    # A real block defines a helper: at least one line that is neither blank
    # nor a bare ``//`` comment.
    return any(
        line.strip() and not line.strip().startswith("//")
        for line in body.splitlines()
    )


class TestSentinelInventory(unittest.TestCase):
    def test_referenced_sentinels_exist_in_index_html(self):
        html = _read_index_html()

        referenced = set()
        for path in sorted(glob.glob(TEST_GLOB)):
            with open(path, encoding="utf-8") as fh:
                referenced |= set(SENTINEL_REF.findall(fh.read()))

        self.assertTrue(referenced, "no pure:NAME sentinels referenced by tests/*.js")

        missing = []
        for name in sorted(referenced):
            if ("// === pure:%s ===" % name) not in html:
                missing.append("%s (open marker)" % name)
            elif ("// === /pure:%s" % name) not in html:
                missing.append("%s (close marker)" % name)

        self.assertEqual(
            missing,
            [],
            "tests/*.js reference sentinels missing from index.html: %s" % missing,
        )

    def test_sentinel_names_are_unique(self):
        html = _read_index_html()

        open_names = OPEN_MARKER.findall(html)
        close_names = CLOSE_MARKER.findall(html)
        self.assertTrue(open_names, "no pure:NAME sentinels found in index.html")

        def duplicates(names):
            return sorted({n for n in names if names.count(n) > 1})

        dup_open = duplicates(open_names)
        dup_close = duplicates(close_names)
        unbalanced = sorted(set(open_names) ^ set(close_names))

        self.assertEqual(
            (dup_open, dup_close, unbalanced),
            ([], [], []),
            "sentinel inventory is inconsistent: "
            "duplicate open=%s, duplicate close=%s, unmatched=%s"
            % (dup_open, dup_close, unbalanced),
        )

    def test_sentinel_blocks_are_not_empty(self):
        html = _read_index_html()

        names = sorted(set(OPEN_MARKER.findall(html)))
        self.assertTrue(names, "no pure:NAME sentinels found in index.html")

        empty = []
        for name in names:
            opened = re.search(r"// === pure:%s ===" % re.escape(name), html)
            closed = re.search(r"// === /pure:%s ===" % re.escape(name), html)
            if opened is None or closed is None:
                continue  # missing/unbalanced markers: covered by the other tests
            if not _has_code(html[opened.end():closed.start()]):
                empty.append(name)

        self.assertEqual(
            empty,
            [],
            "pure sentinel blocks with no code between the markers: %s" % empty,
        )


if __name__ == "__main__":
    unittest.main()
