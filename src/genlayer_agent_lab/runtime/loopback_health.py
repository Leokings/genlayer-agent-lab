"""Credential-free health fetches with a bounded header/body process lifetime."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .container import _bounded_process

# Only stdlib imports run in the isolated child. HTTPConnection ignores proxy
# environment variables and never follows redirects. The parent runner bounds
# trickling headers as well as bodies, without leaving a watchdog thread behind.
# Keep HTTP/1.1 alive through the body and close from this client in finally.
# Asking the service to close first can leave its port in TIME_WAIT and prevent
# the managed service's strict plain-bind check from permitting a quick restart.
_PROBE = """
import http.client
import sys

connection = http.client.HTTPConnection('127.0.0.1', int(sys.argv[1]), timeout=1)
try:
    connection.request('GET', sys.argv[2], headers={'Accept-Encoding': 'identity'})
    response = connection.getresponse()
    if response.status != 200 or response.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
        raise ValueError('unrecognized health response')
    body = response.read(4097)
    if len(body) > 4096:
        raise ValueError('oversized health response')
    sys.stdout.buffer.write(body)
except Exception:
    sys.exit(1)
finally:
    connection.close()
"""


def fetch_health(port, *, nonce=None):
    """Return a small public JSON object, or None; never forward credentials.

    Fetch budget is two seconds plus the runner's bounded startup/cleanup.
    None selects the managed-service health endpoint; a nonce selects setup.
    """
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    if nonce is not None and (type(nonce) is not str or re.fullmatch(r"[0-9a-f]{64}", nonce) is None):
        return None
    path = "/service/health" if nonce is None else "/health?setup_nonce=" + nonce
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    try:
        result = _bounded_process([str(executable), "-I", "-c", _PROBE, str(port), path],
                                  timeout=2, output_limit=4096)
        if result.returncode:
            return None
        value = json.loads(result.stdout)
        return value if type(value) is dict else None
    except (RuntimeError, OSError, ValueError):
        return None
