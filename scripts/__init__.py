"""Command-line tools. Run them from the project folder: python -m scripts.<tool> [options]"""

import sys

# Console output contains emoji and the rupee sign; make it safe on Windows code pages and redirected logs.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
