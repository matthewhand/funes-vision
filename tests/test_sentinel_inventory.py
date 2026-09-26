"""Sentinel-inventory guard for the SPA pure-helper tests (issue #55).

Every tests/*.js Node suite extracts a function from index.html between
``// === pure:NAME ===`` and ``// === /pure:NAME`` markers. If a rename drops
a marker, the owning test fails but nothing tells you the marker is gone
globally. This guard scans the test sources for every referenced ``pure:NAME``
and asserts the matching open/close markers still exist in index.html.

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


class TestSentinelInventory(unittest.TestCase):
    def test_referenced_sentinels_exist_in_index_html(self):
        html = open(INDEX_HTML, encoding="utf-8").read()

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


if __name__ == "__main__":
    unittest.main()
