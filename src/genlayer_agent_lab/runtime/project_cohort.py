"""Journaled controlled-validator configuration for a recoverable project run."""

import copy

from .studio import StudioError
from .studio_cohort import StudioCohort, StudioFixtureLease, _records
from .studio_fixtures import validator_config


class ProjectCohort(StudioCohort):
    def __init__(self, data_dir, client, *, state=None, persist):
        super().__init__(data_dir, client)
        self._lease = StudioFixtureLease(data_dir, subdirectory="studio-modern")
        self._saved_state = copy.deepcopy(state)
        self._persist = persist

    def _owned(self):
        from .studio_profiles import load_profile, modern_profile_status

        state = load_profile(self.data_dir)
        current = modern_profile_status(self.data_dir)
        if (any(current.get(key) is not True for key in
                ("ready", "runtime_verified", "network_internal", "fixture_only"))
                or current.get("owner") != state["owner"]
                or current.get("source_commit") != state["source_commit"]
                or current.get("endpoint") != self.client._provider.url):
            raise StudioError("owned_fixture_stack_required")

    def _journal(self):
        self._persist({"original": copy.deepcopy(self._original),
                       "expected": copy.deepcopy(self._expected),
                       "attempted": copy.deepcopy(self._attempted), "dirty": self._dirty})

    def __enter__(self):
        self._lease.acquire()
        try:
            self._owned()
            with self.client._operation():
                current = self._read()
            if self._saved_state:
                state = self._saved_state
                self._original = _records(list(state["original"].values()))
                self._expected = _records(list(state["expected"].values()))
                self._attempted = (None if state["attempted"] is None
                                   else _records(list(state["attempted"].values())))
                if set(current) != set(self._original):
                    raise StudioError("fixture_cohort_changed")
                for address, record in current.items():
                    permitted = [self._original[address], self._expected[address]]
                    if self._attempted is not None:
                        permitted.append(self._attempted[address])
                    if record not in permitted:
                        raise StudioError("fixture_cohort_changed")
                self._dirty = state["dirty"] is True
                # Interrupted batch updates are completed from the durable target.
                target = self._attempted or self._expected
                with self.client._operation():
                    for address, record in target.items():
                        if current[address] != record:
                            self._update(record)
                    if self._read() != target:
                        raise StudioError("fixture_configuration_not_verified")
                self._expected = copy.deepcopy(target)
                self._attempted = None
            else:
                self._original = self._expected = copy.deepcopy(current)
                self._dirty = False
            self._active = True
            self._journal()
            return self
        except BaseException:
            self._lease.close()
            raise

    def apply(self, prompts):
        if not self._active:
            raise StudioError("fixture_cohort_not_open")
        mocks = validator_config(prompts=prompts)["plugin_config"]["mock_response"]
        proposed = copy.deepcopy(self._expected)
        for record in proposed.values():
            record["plugin_config"]["mock_response"] = copy.deepcopy(mocks)
        _records(list(proposed.values()))
        self._owned()
        with self.client._operation():
            if self._read() != self._expected:
                raise StudioError("fixture_cohort_changed")
            self._attempted, self._dirty = proposed, True
            self._journal()  # Commit before the first validator update.
            for address, record in proposed.items():
                if record != self._expected[address]:
                    self._update(record)
            if self._read() != proposed:
                raise StudioError("fixture_configuration_not_verified")
            self._expected, self._attempted = proposed, None
            self._journal()
        return {"validator_count": len(proposed), "stored_configuration_verified": True}

    def close(self):
        if not self._active:
            return
        # Preserve the original until restore is fully observed, so a crash during
        # cleanup can resume with the same original/expected/attempted allowlist.
        super().close()
        self._persist(None)
