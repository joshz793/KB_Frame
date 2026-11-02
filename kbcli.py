#!/usr/bin/env python3
'''Run kbcli without installing the package (`pip install .` provides the `kbcli` command instead).'''
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
from lessons_kb.cli import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
