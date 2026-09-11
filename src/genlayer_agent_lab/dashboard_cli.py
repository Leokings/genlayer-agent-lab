"""Owner sign-in approval, without displaying or initializing any credential."""

import re

import httpx

from .api import read_admin_token
from .onboarding_setup import _port_state


def approve_browser(data_dir, code, *, port=8765):
    if not 1024 <= port <= 65535:
        raise ValueError("Choose the port used by this Lab installation.")
    code = code.upper().replace("-", "").replace(" ", "")
    if re.fullmatch(r"[A-Z0-9]{12}", code) is None:
        raise ValueError("Copy the complete sign-in code from your browser.")
    if _port_state(data_dir, port) != "same_installation":
        raise ValueError("This Lab is not reachable. Ask your setup agent to start the existing installation.")
    # Never send an owner credential until the existing installation answered its
    # fresh challenge. Do not create missing directories, follow redirects or
    # forward the credential through environment proxies.
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        try:
            response = client.post(f"http://127.0.0.1:{port}/v1/dashboard/approve",
                                   json={"code": code},
                                   headers={"Authorization": "Bearer " + read_admin_token(data_dir)})
        except httpx.HTTPError:
            raise ValueError("The Lab did not respond. Check your browser before retrying approval.") from None
    if response.status_code != 200:
        raise ValueError("Sign-in could not be approved. Request a new code in your browser and try again.")
    print("Browser approved. Return to your dashboard; it will sign you in automatically.")
    return 0
