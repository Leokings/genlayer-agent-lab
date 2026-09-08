"""Assemble supplied-artifact releases; never publish or include installation state."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tarfile
import tomllib
import zipfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "dist" / f"release-{version}")
    parser.add_argument("--source-commit", help="Explicit verified 40-character project commit, never inferred from a parent repo")
    args = parser.parse_args()
    if args.source_commit and not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        parser.error("--source-commit must be a lowercase 40-character Git commit")
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise SystemExit("Choose a new output directory; release bundles are not overwritten.")
    basename = f"genlayer_agent_lab-{version}"
    wheel = root / "dist" / f"{basename}-py3-none-any.whl"
    source = root / "dist" / f"{basename}.tar.gz"
    required_kit = {"docs/INSTALL.md", "docs/SERVICES.md", "docs/RECOVERY.md",
                    "docs/STUDIO.md", "docs/EXTERNAL_ONBOARDING.md",
                    "docs/PROJECT_WORKFLOWS.md", "docs/PROJECT_BINDINGS.md", "docs/SCENARIO_AUTHORING.md",
                    "examples/projects/prediction/project.yaml",
                    "examples/projects/prediction-messages/project-repair.yaml",
                    "examples/project-scenarios/prediction-finalize.yaml",
                    "examples/python/project_agent.py", "examples/typescript/project-agent.ts",
                    "examples/mcp_project_agent.py",
                    "examples/python_agent.py", "examples/typescript/agent.ts", "examples/mcp_agent.py",
                    "skills/setup-genlayer-agent-lab/SKILL.md"}
    with zipfile.ZipFile(wheel) as package:
        names = set(package.namelist())
        required = {"genlayer_agent_lab/_kit/" + name for name in required_kit}
        if not required <= names:
            raise SystemExit("Wheel is missing required setup kit resources; rebuild after completing docs.")
    with tarfile.open(source, "r:gz") as package:
        source_names = set(package.getnames())
    for name in names | source_names:
        parts = Path(name).parts
        if any(part in {".lab", ".venv", "node_modules", ".git", "__pycache__"} for part in parts):
            raise SystemExit("An artifact includes non-release state; inspect the build configuration.")
        if any(part in {"admin.token", ".env", ".env.local"} or part.endswith((".pyc", ".log"))
               for part in parts):
            raise SystemExit("An artifact includes a private/generated file; inspect the build configuration.")
    if f"{basename}/uv.lock" not in source_names:
        raise SystemExit("Source archive must include uv.lock.")
    output.mkdir(parents=True, mode=0o700)
    items = [wheel, source, root / "docs" / "INSTALL.md",
             root / "skills" / "setup-genlayer-agent-lab" / "SKILL.md"]
    manifest = []
    for item in items:
        target = output / item.name
        shutil.copyfile(item, target)
        with target.open("rb") as reader:
            digest = hashlib.file_digest(reader, "sha256").hexdigest()
        manifest.append({"file": target.name, "bytes": target.stat().st_size,
                         "sha256": digest})
    (output / "SHA256SUMS").write_text(
        "".join(f"{item['sha256']}  {item['file']}\n" for item in manifest), encoding="utf-8")
    record = {"version": version, "source_commit": args.source_commit, "artifacts": manifest,
              "prepared_by": "prepare-release; this command does not publish",
              "notice": "Developer alpha. Consult packaged BUILD_STATUS and VERIFICATION for release gates."}
    (output / "release.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **record}, indent=2))


if __name__ == "__main__":
    main()
