# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Make check-docstrings' modules importable by its tests.

pytest's ``importlib`` import mode does not put a test's directory on
``sys.path``, and the project that vendors this tool may not list it in its
``pythonpath`` setting, so the tests could not import the script they cover.
Adding the directory here keeps the tool self-contained when it is copied.

Dependencies:
    sys, pathlib: Put this directory at the front of ``sys.path``.

Safety:
    Changes only the pytest process's import path.

Example:
    $ uv run pytest tools/check-docstrings
"""

import sys
from pathlib import Path

HERE = str(Path(__file__).parent)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
