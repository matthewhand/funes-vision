// Entry point for the loader self-test. tests/run.sh globs tests/*.js, so the
// suite that proves tests/helpers/load.cjs throws on a miss is one line here and
// the fixture lives next to the loader it exercises.
// Run: node tests/test_helpers_selftest.js
require('./helpers/selftest.js');
