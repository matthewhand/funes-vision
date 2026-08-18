// Node assert tests for the pure SSE-merge helpers in index.html that let the
// gallery update from live events WITHOUT a full-file refetch:
//   mergeNewImage (insert newest frame, dedup) and detectionEntry (build an
//   analysis.json-shaped entry from a detection event). Run: node tests/test_sse_dom.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
function block(name) {
  const m = html.match(new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*\\/\\/ === \\/pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}
eval(block('mergeNewImage'));
eval(block('detectionEntry'));

// --- mergeNewImage: newest-first prepend, deduped, immutable ---
assert.deepStrictEqual(mergeNewImage(['b', 'a'], 'c'), ['c', 'b', 'a']); // newest at front
assert.deepStrictEqual(mergeNewImage([], 'x'), ['x']);
const same = ['b', 'a'];
assert.strictEqual(mergeNewImage(same, 'a'), same);  // already present -> same ref (no-op)
assert.strictEqual(mergeNewImage(same, ''), same);   // blank -> no-op
const orig = ['b', 'a'];
const out = mergeNewImage(orig, 'c');
assert.deepStrictEqual(orig, ['b', 'a']);            // original not mutated
assert.notStrictEqual(out, orig);
assert.deepStrictEqual(mergeNewImage(null, 'x'), ['x']); // defensive

// --- detectionEntry: matches computeLabelStates' shape ---
assert.deepStrictEqual(detectionEntry(['person', 'car'], true),
  { person: true, car: true, fast_pass: 'partial' });        // preliminary
assert.deepStrictEqual(detectionEntry(['dog'], false),
  { dog: true, _yolo: ['dog'], _llm: {} });                  // verified (optimistic corroboration)
assert.deepStrictEqual(detectionEntry([], true), { fast_pass: 'partial' });
assert.deepStrictEqual(detectionEntry(null, false), { _yolo: [], _llm: {} });

// Round-trip: a preliminary entry reads back as 'preliminary' via computeLabelStates.
eval(block('labelStates'));
const st = computeLabelStates(detectionEntry(['person'], true), {});
assert.strictEqual(st.get('person'), 'preliminary');
const st2 = computeLabelStates(detectionEntry(['person'], false), {});
assert.strictEqual(st2.get('person'), 'verified');

console.log('sse-dom: all assertions passed');
