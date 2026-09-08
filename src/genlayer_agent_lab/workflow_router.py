"""Keep existing workflow clients usable with either supported schema version."""

from .project_workflows import ProjectWorkflowManager
from .workflows import WorkflowManager


class WorkflowRouter:
    def __init__(self, store, data_dir):
        self.legacy = WorkflowManager(store, data_dir)
        self.projects = ProjectWorkflowManager(store, data_dir)

    def _manager(self, run_id):
        return self.projects if run_id.startswith("project-") else self.legacy

    def create(self, spec):
        manager = self.projects if spec.get("schema_version") == 2 else self.legacy
        return manager.create(spec)

    def list_runs(self):
        return sorted(self.legacy.list_runs() + self.projects.list_runs(),
                      key=lambda run: run.get("created_at", 0), reverse=True)

    def get(self, run_id):
        return self._manager(run_id).get(run_id)

    def observe(self, run_id):
        return self._manager(run_id).observe(run_id)

    def authenticate(self, run_id, token):
        return self._manager(run_id).authenticate(run_id, token)

    def reject_invalid_action(self, run_id):
        return self._manager(run_id).reject_invalid_action(run_id)

    def invoke(self, run_id, operation, arguments, idempotency_key, expected_decision_id=None):
        return self._manager(run_id).invoke(run_id, operation, arguments, idempotency_key,
                                            expected_decision_id=expected_decision_id)

    def appeal(self, run_id, key, expected_decision_id):
        return self._manager(run_id).appeal(run_id, key, expected_decision_id)

    def finish(self, run_id):
        return self._manager(run_id).finish(run_id)

    def cancel(self, run_id):
        return self._manager(run_id).cancel(run_id)

    def report(self, run_id):
        return self._manager(run_id).report(run_id)

    def close(self):
        self.projects.close()
        self.legacy.close()
