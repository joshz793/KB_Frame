"""The Claude Code plugin manifest matches the package version.

    python tests/test_plugin.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with open(os.path.join(ROOT, 'src', 'lessons_kb', '__init__.py')) as f:
        pkg = re.search(r"__version__ = '([^']+)'", f.read()).group(1)
    with open(os.path.join(ROOT, '.claude-plugin', 'plugin.json')) as f:
        plugin = json.load(f)['version']
    assert plugin == pkg, 'plugin.json is %s, package is %s' % (plugin, pkg)
    assert os.path.isfile(os.path.join(ROOT, 'agents', 'kb-reconciler.md'))
    skill = os.path.join(ROOT, 'skills', 'lessons-kb', 'SKILL.md')
    with open(skill) as f:
        head = f.read().split('---')[1]
    assert re.search(r'^name: lessons-kb$', head, re.M), 'SKILL.md name must be lessons-kb'
    assert re.search(r'^description: \S', head, re.M), 'SKILL.md needs a description'
    print('OK')


if __name__ == '__main__':
    sys.exit(main())
