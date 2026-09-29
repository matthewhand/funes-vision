// Contract: the section map in index.html's opening comment must describe the
// file it sits in.
//
// THE ROT THIS PINS. The map lists every sentinel-wrapped pure helper per
// region, and the list is hand-maintained. `timeStampLabel` shipped with its
// marker pair and was never added to region C, so the map claimed 95 pairs
// while the file had 96; docs/diagrams/14 quoted the 95 as a measured figure
// and carried it until PR #102 re-derived the number from source. Nothing
// compared the comment to the file, so the same edit can re-seed the drift. A
// count would be no better: it hides WHICH name drifted.
//
// So the check is SET-EQUALITY, not a count. A sentinel added to the file
// without being listed, listed without existing, renamed, moved to the wrong
// region, listed twice, or re-ordered out of file order all fail here with the
// names in the message. The member lists get the same treatment: a name the map
// names has to still be declared in index.html, so a rename that misses the map
// cannot land.
//
// The map is prose -- a region line is `X. Title  member / member / member`, and
// the title carries English words too -- so the member extractor takes a token
// as a NAME when it is shaped like code (camelCase, or SCREAMING_SNAKE) and
// ignores the rest. Three real members are invisible to that shape rule
// (`cameras`, `state` in region A/B, `POPOVERS` in G's impure list); they are
// listed in LOWERCASE_MEMBERS and asserted declared like everything else. The
// floor on how many names the extractor found keeps it from quietly returning
// nothing and passing on an empty set -- the failure mode tests/helpers/load.cjs
// exists to prevent.
//
// NOT duplicated here: tests/test_sentinel_inventory.py already proves the
// markers exist, are unique, are balanced and are non-empty, and that every
// sentinel a test references is present. This file adds the other direction --
// the map agrees with the markers -- which nothing checked.
const assert = require('assert');
const { src, createLoader } = require('./helpers/load.cjs');

const REGION = /^ {2,3}([A-S])\.\s/;
const SENTINELS = /^\s{0,6}sentinels:/;
const IDENT = /^[A-Za-z_$][A-Za-z0-9_$]*$/;

// A token shaped like a JS name rather than an English word: camelCase, or a
// SCREAMING_SNAKE constant. No region title in the map has either, which is
// what keeps "SSE status" and "DOM binding" out of the member list.
const CODE_SHAPED = (w) => /^[a-z_$][A-Za-z0-9_$]*[A-Z]/.test(w) || (/^[A-Z][A-Z0-9_]*$/.test(w) && w.includes('_'));

// Real members the shape rule cannot see. Each is asserted declared, so a
// rename of one of these fails here too.
const LOWERCASE_MEMBERS = ['cameras', 'state', 'POPOVERS'];

// Floors on what the two extractors must find, so a parse that silently stopped
// matching cannot leave a comparison running on an empty set.
const NAME_FLOOR = 100;
const SENTINEL_FLOOR = 90;

function check(html, label) {
  const L = createLoader(html, label);
  const srcText = L.src;

  // The map is the first HTML comment in the file; without one there is no
  // table of contents to check, and every assertion below would go vacuous.
  const at = srcText.indexOf('<!--');
  const end = srcText.indexOf('-->');
  assert.ok(
    at !== -1 && end > at,
    `${label}: index.html must open with the section-map comment`
  );
  const map = srcText.slice(at + 4, end).split('\n');
  assert.ok(
    map.slice(0, 4).join(' ').includes('index.html — single-file SPA'),
    `${label}: the leading comment must still be the section map, not something else`
  );

  // ---- regions -------------------------------------------------------------
  const regions = [];
  map.forEach((line, i) => {
    const m = REGION.exec(line);
    if (m) regions.push({ letter: m[1], line: i });
  });
  assert.deepStrictEqual(
    regions.map((r) => r.letter).join(''),
    'ABCDEFGHIJKLMNOPQRS',
    `${label}: the map must still list regions A-S in file order`
  );
  regions.forEach((r, i) => {
    r.end = i + 1 < regions.length ? regions[i + 1].line : map.length;
  });

  // ---- the sentinel lists, in map order ------------------------------------
  const listed = {};
  for (const r of regions) {
    const names = [];
    const push = (text) => {
      for (const w of text.split(/\s+/)) if (IDENT.test(w)) names.push(w);
    };
    for (let i = r.line; i < r.end; i++) {
      if (!SENTINELS.test(map[i])) continue;
      push(map[i].replace(/^\s*sentinels:\s*/, ''));
      for (let j = i + 1; j < r.end && /^\s{7,}\S/.test(map[j]); j++) {
        push(map[j].trim());
      }
    }
    listed[r.letter] = names;
  }

  const dupesIn = (names) => {
    const seen = new Set();
    return names.filter((n) => (seen.has(n) ? true : (seen.add(n), false)));
  };
  for (const [letter, names] of Object.entries(listed)) {
    assert.deepStrictEqual(
      dupesIn(names),
      [],
      `${label}: region ${letter} lists the same sentinel twice`
    );
  }

  // ---- the markers, in file order -----------------------------------------
  const markers = [];
  for (const line of srcText.split('\n')) {
    const m = /^\s*\/\/ === pure:([A-Za-z0-9_$]+) ===\s*$/.exec(line);
    if (m) markers.push(m[1]);
  }
  // The two comparisons below are symmetric, so two empty sets would compare
  // equal; the floor stops that from reading as a pass.
  assert.ok(
    markers.length >= SENTINEL_FLOOR,
    `${label}: only ${markers.length} sentinel markers parsed out of index.html -- ` +
      'the extractor stopped matching and the comparisons below prove nothing'
  );
  const all = [...new Set(Object.values(listed).flat())];

  assert.deepStrictEqual(
    markers.filter((n) => !all.includes(n)),
    [],
    `${label}: index.html wraps a pure helper in sentinel markers the section ` +
      'map does not list. Add it to its region (sentinels:, in file order) or the ' +
      'map is stale.'
  );
  assert.deepStrictEqual(
    all.filter((n) => !markers.includes(n)),
    [],
    `${label}: the section map lists a sentinel index.html does not have. ` +
      'Remove it or the map is stale.'
  );

  for (const [letter, names] of Object.entries(listed)) {
    const at2 = names.map((n) => markers.indexOf(n));
    const order = at2.filter((i) => i >= 0);
    assert.deepStrictEqual(
      order,
      [...order].sort((a, b) => a - b),
      `${label}: region ${letter} lists its sentinels out of file order: ${names.join(' ')}`
    );
  }

  // ---- the member names ----------------------------------------------------
  const declared = (n) =>
    new RegExp('(?:^|[\\s;{(,])(?:async )?(?:function|const|let|var|class)\\s+' + n + '\\b')
      .test(srcText);

  const missing = [];
  let checked = 0;
  for (const r of regions) {
    for (let i = r.line; i < r.end; i++) {
      const line = map[i];
      if (SENTINELS.test(line)) {
        while (i < r.end && (/^ {0,6}sentinels:/.test(map[i]) || /^ {7,}\S/.test(map[i]))) i++;
        i--;
        continue;
      }
      if (i > r.line && (/^\s{0,4}\S/.test(line) || /^\s*$/.test(line))) break;
      const text = (i === r.line ? line.replace(REGION, ' ') : line)
        .replace(/\([^()]*\)/g, ' ')
        .replace(/^\s*also:\s*/, ' ');
      for (const w of text.split(/[\s/]+/)) {
        if (!IDENT.test(w)) continue;
        if (!CODE_SHAPED(w) && !LOWERCASE_MEMBERS.includes(w)) continue;
        checked++;
        if (!declared(w)) missing.push(`${r.letter}: ${w}`);
      }
    }
  }
  assert.deepStrictEqual(
    missing,
    [],
    `${label}: the section map names these members, which index.html does not ` +
      `declare (renamed or removed?): ${missing.join(', ')}`
  );
  assert.ok(
    checked >= NAME_FLOOR,
    `${label}: only ${checked} member names parsed out of the map -- the ` +
      'extractor stopped matching and the member assertions prove nothing'
  );
  for (const n of LOWERCASE_MEMBERS) {
    assert.ok(declared(n), `${label}: ${n} is listed in LOWERCASE_MEMBERS but is gone`);
  }
}

check(src, 'index.html');

// ---- proofs the checks can fail -------------------------------------------
// tests/test_sentinel_inventory.py reads every tests/*.js for a `pure:` marker
// followed by a name and requires that marker to exist in index.html, so the
// mutants below cannot spell a fictitious name out -- they build it from MARK.
const MARK = 'pure' + ':';

// The two defects this file exists for, re-run against a deliberately broken
// copy of the source through createLoader(). An assertion nobody can make fail
// is not a test, so both are proved here with assert.throws.
assert.throws(
  () => check(src.replace('hourInZone timeStampLabel\n', 'hourInZone\n'), 'sentinel-omitted'),
  /index\.html wraps a pure helper in sentinel markers the section map does not list/,
  'dropping timeStampLabel from region C must fail -- that omission IS the bug'
);
assert.throws(
  () => check(src.replace('function isActivateKey(', 'function isActivateKeyRenamed('), 'member-renamed'),
  /names these members, which index\.html does not declare/,
  'renaming a function the map names must fail, or a rename lands silently'
);

// Every mutant below is a copy of index.html with one realistic edit to the
// drift under test, including the two above in the shapes the checks see them
// in. If check() accepts any of them the assertions above prove nothing.
const MUTANTS = {
  'omit timeStampLabel from region C': src.replace(
    'hourInZone timeStampLabel\n',
    'hourInZone\n'
  ),
  'add a sentinel the map does not list': src.replace(
    `    // === ${MARK}isActivateKey ===`,
    `    // === ${MARK}brandNew ===\n` +
      '    function brandNew() { return 1; }\n' +
      `    // === /${MARK}brandNew ===\n\n` +
      `    // === ${MARK}isActivateKey ===`
  ),
  'list a sentinel the file does not have': src.replace(
    '       lightboxFooterMeta showSearchClear isActivateKey',
    '       lightboxFooterMeta showSearchClear isActivateKey brandNewHelper'
  ),
  'rename a sentinel in the file only': src
    .replace(`    // === ${MARK}escapeHtml ===`, `    // === ${MARK}escapeHtmlX ===`)
    .replace(`    // === /${MARK}escapeHtml ===`, `    // === /${MARK}escapeHtmlX ===`),
  'file a sentinel under the wrong region': src.replace(
    '       lightboxFooterMeta showSearchClear isActivateKey',
    '       lightboxFooterMeta isActivateKey showSearchClear'
  ),
  'list a sentinel twice': src.replace(
    '       lightboxFooterMeta showSearchClear isActivateKey',
    '       lightboxFooterMeta lightboxFooterMeta showSearchClear isActivateKey'
  ),
  'list a region out of file order': src.replace(
    '       shortTimeZoneName parseFilenameFields filenamesOnDate visitAltText',
    '       parseFilenameFields shortTimeZoneName filenamesOnDate visitAltText'
  ),
  'rename a function the map names': src.replace(
    'function isActivateKey(',
    'function isActivateKeyRenamed('
  ),
  'drop a region from the map': src.replace(
    '  R. DOMContentLoaded, 2 of 3',
    '  R2. DOMContentLoaded, 2 of 3'
  ),
  'delete the map comment': src.replace('<!--', '<p>--'),
};

let killed = 0;
for (const [name, mutant] of Object.entries(MUTANTS)) {
  assert.notStrictEqual(mutant, src, `mutation "${name}" did not change the source`);
  let failure = null;
  try {
    check(mutant, name);
  } catch (e) {
    failure = e;
  }
  assert.ok(
    failure,
    `mutant "${name}" SURVIVED -- check() accepted the drifted document, so ` +
      'the assertions above cannot fail and prove nothing'
  );
  killed++;
}
assert.strictEqual(killed, Object.keys(MUTANTS).length, 'every mutant must be caught');

console.log(`section map: all assertions passed (${killed} mutants killed)`);
