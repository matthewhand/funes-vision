"""Strict duplicate-key and shape check for the GitHub Actions workflow file.

PyYAML silently accepts a repeated mapping key, which GitHub Actions then
rejects at run time with "This run likely failed because of a workflow file
issue". This guard makes that a local failure instead.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/synthetic-fixtures.yml'

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses a repeated key instead of keeping the last one."""


def _no_duplicates(loader, node, deep=False):
    seen = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                'while constructing a mapping', node.start_mark,
                f'duplicate key {key!r} (first seen at line {seen[key]})',
                key_node.start_mark)
        seen[key] = key_node.start_mark.line + 1
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)

text = WORKFLOW.read_text(encoding='utf-8')
workflow = yaml.load(text, Loader=StrictLoader)

jobs = workflow['jobs']
assert jobs, 'no jobs defined'
for name, job in jobs.items():
    assert job.get('runs-on'), f'{name}: missing runs-on'
    assert job.get('steps'), f'{name}: missing steps'
    for step in job['steps']:
        assert 'uses' in step or 'run' in step, f'{name}: step does nothing'
        assert not ('uses' in step and 'run' in step), f'{name}: step has both uses and run'
assert set(jobs) >= {'verify', 'docker-preview', 'python-single-origin', 'browser',
                     'python-single-origin-docker'}
assert workflow['permissions'] == {'contents': 'read'}
print(f'PASS workflow: {len(jobs)} jobs, no duplicate keys, read-only permissions')