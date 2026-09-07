"""Exclusive fixture configuration for an owned, isolated Studio installation.

Validator records stay private. The supported owned RPC patch only forwards the
existing ``config`` argument; no consensus state is changed by this helper.
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
from pathlib import Path

from ..bindings import bounded_json
from . import studio_stack
from .studio import StudioError
from .studio_fixtures import FIXTURE_API_URL, FIXTURE_KEY_ENV, validator_config

MIN_VALIDATORS = 12
MAX_VALIDATORS = 64
_FIELDS = ("address", "stake", "provider", "model", "config", "plugin", "plugin_config")
_IDENTITY = ("address", "stake", "provider", "model", "config", "plugin")


class StudioFixtureLease:
    """Nonblocking process-wide lease, shared with Studio lifecycle operations."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._handle = None

    def acquire(self):
        if self._handle is not None:
            raise StudioError("fixture_lease_already_acquired")
        try:
            root = studio_stack._root(self.data_dir)
            root.mkdir(parents=True, exist_ok=True)
            path = root / "fixture.lock"
            if path.is_symlink():
                raise StudioError("invalid_fixture_lease")
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            handle = os.fdopen(descriptor, "r+b", buffering=0)
            try:
                metadata = os.fstat(handle.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise StudioError("invalid_fixture_lease")
                if metadata.st_size == 0:
                    handle.write(b"0")
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise StudioError("studio_fixture_busy") from None
            except BaseException:
                handle.close()
                raise
            self._handle = handle
            return self
        except StudioError:
            raise
        except Exception:
            raise StudioError("invalid_fixture_lease") from None

    def close(self):
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.close()


def _records(raw):
    """Copy only supported fixture fields, never keys or arbitrary RPC metadata."""
    try:
        if type(raw) is not list or not MIN_VALIDATORS <= len(raw) <= MAX_VALIDATORS:
            raise ValueError
        result = {}
        for item in raw:
            if type(item) is not dict:
                raise ValueError
            record = {field: copy.deepcopy(item[field]) for field in _FIELDS}
            address = record["address"]
            if (type(address) is not str or not re.fullmatch(r"0x[0-9a-fA-F]{40}", address)
                    or address.lower() in result or type(record["stake"]) is not int
                    or not 0 < record["stake"] <= 2**256 - 1
                    or record["provider"] != "openai" or record["model"] != "gpt-4o"
                    or record["plugin"] != "openai-compatible"
                    or record["config"] != {"temperature": 0}):
                raise ValueError
            plugin = record["plugin_config"]
            if (type(plugin) is not dict
                    or set(plugin) != {"api_key_env_var", "api_url", "mock_response"}
                    or plugin["api_key_env_var"] != FIXTURE_KEY_ENV
                    or plugin["api_url"] != FIXTURE_API_URL):
                raise ValueError
            mocks = plugin["mock_response"]
            if (type(mocks) is not dict
                    or set(mocks) != {"response", "eq_principle_prompt_comparative",
                                     "eq_principle_prompt_non_comparative"}
                    or mocks["eq_principle_prompt_comparative"] != {}
                    or mocks["eq_principle_prompt_non_comparative"] != {}
                    or type(mocks["response"]) is not dict
                    or not 1 <= len(mocks["response"]) <= 32):
                raise ValueError
            bounded_json(record)
            if len(json.dumps(record).encode()) > 131072:
                raise ValueError
            result[address.lower()] = record
        return result
    except Exception:
        raise StudioError("invalid_fixture_cohort") from None


class StudioCohort:
    """Hold the fixture lease until all dependent nonfinal work is finished.

    Each apply/restore is bounded by one client operation and at most 64 updates.
    Restores run before releasing the lease, including when the caller raises.
    Close the cohort before closing or cancelling its client.
    """

    def __init__(self, data_dir: Path, client):
        self.data_dir = Path(data_dir)
        self.client = client
        self._lease = StudioFixtureLease(data_dir)
        self._original = None
        self._expected = None
        self._attempted = None
        self._active = False
        self._dirty = False

    def _owned(self):
        try:
            state = studio_stack._load(studio_stack._root(self.data_dir))
            current = studio_stack.status(self.data_dir)
            if (any(current.get(field) is not True for field in
                    ("installed", "ready", "runtime_verified", "network_internal",
                     "fixture_only", "fixture_config_patch"))
                    or current.get("public_chain") is not False
                    or current.get("source_commit") != studio_stack.STUDIO_COMMIT
                    or current.get("project") != "gl-agent-lab-" + state["owner"]
                    or current.get("endpoint") != f"http://127.0.0.1:{state['port']}"
                    or self.client._provider.url != current["endpoint"]):
                raise ValueError
        except Exception:
            raise StudioError("owned_fixture_stack_required") from None

    def _read(self):
        try:
            return _records(self.client._rpc("sim_getAllValidators", []))
        except StudioError:
            raise
        except Exception:
            raise StudioError("fixture_cohort_read_failed") from None

    def __enter__(self):
        if self._active:
            raise StudioError("fixture_cohort_already_open")
        self._lease.acquire()
        try:
            self._owned()
            with self.client._operation():
                self._original = self._read()
            self._expected = copy.deepcopy(self._original)
            self._dirty = False
            self._active = True
            return self
        except BaseException:
            self._lease.close()
            raise

    def _update(self, record):
        # The owned compatibility patch forwards config. Unpatched upstream
        # rejects this explicit parameter rather than silently nulling config.
        params = {key: copy.deepcopy(record[key]) for key in _FIELDS if key != "address"}
        params["validator_address"] = record["address"]
        try:
            self.client._rpc("sim_updateValidator", params)
        except Exception:
            raise StudioError("fixture_update_failed") from None

    def apply(self, prompts: dict) -> dict:
        if not self._active:
            raise StudioError("fixture_cohort_not_open")
        try:
            bounded_json(prompts)
            mocks = validator_config(prompts=prompts)["plugin_config"]["mock_response"]
            proposed = copy.deepcopy(self._expected)
            for record in proposed.values():
                record["plugin_config"]["mock_response"] = copy.deepcopy(mocks)
            _records(list(proposed.values()))
        except Exception:
            raise StudioError("invalid_cohort_prompts") from None
        self._owned()
        with self.client._operation():
            if self._read() != self._expected:
                raise StudioError("fixture_cohort_changed")
            self._attempted = proposed
            self._dirty = True  # A failed response may follow a committed write.
            for address, record in proposed.items():
                if record != self._expected[address]:
                    self._update(record)
            if self._read() != proposed:
                raise StudioError("fixture_configuration_not_verified")
            self._expected = proposed
            self._attempted = None
        return {"validator_count": len(proposed), "stored_configuration_verified": True}

    def close(self):
        if not self._active:
            return
        failed = False
        try:
            if self._dirty:
                self._owned()
                with self.client._operation():
                    current = self._read()
                    if set(current) != set(self._original):
                        raise StudioError("fixture_cohort_changed")
                    for address, record in current.items():
                        if any(record[key] != self._original[address][key] for key in _IDENTITY):
                            raise StudioError("fixture_cohort_changed")
                        permitted = [self._original[address], self._expected[address]]
                        if self._attempted is not None:
                            permitted.append(self._attempted[address])
                        if record not in permitted:
                            raise StudioError("fixture_cohort_changed")
                    # Continue after an individual response failure, but never
                    # extend the client's overall operation deadline.
                    for address, record in self._original.items():
                        if current[address] != record:
                            try:
                                self._update(record)
                            except Exception:
                                failed = True
                    if self._read() != self._original:
                        failed = True
        except Exception:
            failed = True
        finally:
            self._active = False
            self._original = self._expected = self._attempted = None
            self._lease.close()
        if failed:
            raise StudioError("fixture_restore_failed") from None

    def abandon(self):
        """Release ownership without touching fixtures when work may be nonfinal.

        The caller must persist unresolved cleanup and block another session.
        This is explicitly not restoration and performs no RPC operation.
        """
        self._active = False
        self._dirty = False
        self._original = self._expected = self._attempted = None
        self._lease.close()

    def __exit__(self, *_):
        self.close()
