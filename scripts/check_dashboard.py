"""Diagnostic used by check_dashboard.cmd: is the local dashboard server answering?"""

import urllib.request

URL = "http://127.0.0.1:8765/healthz"

try:
    response = urllib.request.urlopen(URL, timeout=5)
    print("HTTP", response.status, response.read().decode())
except Exception as exc:  # noqa: BLE001 - report any failure as text
    print("ERROR", repr(exc))
