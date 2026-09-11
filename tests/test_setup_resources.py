"""The pre-install instructions must work in both source and exported installs."""

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from genlayer_agent_lab.api import create_app, read_admin_token
from genlayer_agent_lab.kit import export_kit

ROOT = Path(__file__).parents[1]


def test_setup_page_is_public_but_does_not_contain_workspace_credentials(tmp_path):
    app = create_app(tmp_path, engine=SimpleNamespace(workflows=SimpleNamespace()))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get("/setup")
        assert response.status_code == 200
        assert response.content == (ROOT / "docs" / "START.html").read_bytes()
        assert read_admin_token(tmp_path) not in response.text
        assert client.get("/v1/onboarding/templates").status_code == 401


def test_exported_kit_carries_standalone_page_and_exact_bootstrap(tmp_path):
    output = tmp_path / "kit"
    result = export_kit(output)
    assert Path(result["setup_page"]) == output / "docs" / "START.html"
    authoring_skill = Path("skills") / "prepare-genlayer-lab-test" / "SKILL.md"
    assert Path(result["authoring_skill"]) == output / authoring_skill
    assert (output / authoring_skill).read_bytes() == (ROOT / authoring_skill).read_bytes()
    for guide in ("SCENARIO_AUTHORING.md", "PROJECT_BINDINGS.md"):
        assert (output / "docs" / guide).read_bytes() == (ROOT / "docs" / guide).read_bytes()
    assert (output / "scripts" / "bootstrap-ubuntu.sh").read_bytes() == (
        ROOT / "scripts" / "bootstrap-ubuntu.sh").read_bytes()
    assert (output / "docs" / "START.html").read_bytes() == (ROOT / "docs" / "START.html").read_bytes()
    # The small bootstrap ships without unrelated maintainer/CI programs.
    assert {path.name for path in (output / "scripts").iterdir()} == {"bootstrap-ubuntu.sh"}
