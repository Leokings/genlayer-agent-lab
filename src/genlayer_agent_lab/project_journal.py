"""Private durable run material, separate from agent observations and reports."""

import base64
import hashlib
import json
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from eth_account import Account

from .api import read_admin_token


class ProjectVault:
    """Encrypt local test signers/envelopes with the installation credential.

    This protects accidental database/report copying without the admin token.
    The installation owner can decrypt it; this is not an external key manager.
    Changing admin.token requires migrating the journal before restarting runs.
    """

    def __init__(self, data_dir: Path):
        token = read_admin_token(Path(data_dir))
        key = hashlib.sha256(b"genlayer-agent-lab/project-journal/v2\0" + token.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def seal(self, value: dict) -> str:
        raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > 2_000_000:
            raise ValueError("Private workflow journal exceeds size limit")
        return self._fernet.encrypt(raw).decode("ascii")

    def open(self, value: str) -> dict:
        try:
            result = json.loads(self._fernet.decrypt(value.encode("ascii")))
            if type(result) is not dict:
                raise ValueError
            return result
        except (InvalidToken, UnicodeError, ValueError, TypeError):
            raise ValueError("Cannot recover workflow journal with this installation credential") from None

    def new_account(self) -> dict:
        account = Account.create()
        return {"address": account.address, "private_key": "0x" + account.key.hex().removeprefix("0x")}
