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
    print('OK')


if __name__ == '__main__':
    sys.exit(main())
