"""Export the documentation and integration examples carried inside the wheel."""

from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path

from . import __version__


def export_kit(output: Path) -> dict:
    output = output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Kit output must be a new directory; existing files are never replaced.")
    packaged = files("genlayer_agent_lab").joinpath("_kit")
    if packaged.is_dir():
        roots = list(packaged.iterdir())
    else:
        # Editable installs use the same maintained files as the wheel build.
        checkout = Path(__file__).resolve().parents[2]
        if not (checkout / "pyproject.toml").is_file():
            raise RuntimeError("Installation kit resources are missing; reinstall the complete wheel.")
        roots = [checkout / name for name in
                 ("README.md", "LICENSE", "THIRD_PARTY.md", "docs", "examples", "skills")]
    count = 0

    def copy(source, destination: Path) -> None:
        nonlocal count
        if source.name == "__pycache__" or source.name.endswith((".pyc", ".pyo")):
            return
        if isinstance(source, Path) and source.is_symlink():
            raise ValueError("Kit sources must not contain symlinks.")
        if source.is_dir():
            destination.mkdir()
            for child in source.iterdir():
                copy(child, destination / child.name)
        elif source.is_file():
            with source.open("rb") as reader, destination.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
            count += 1
        else:
            raise ValueError("Kit resource is missing or is not a regular file.")

    output.mkdir(parents=True, mode=0o700)
    for root in roots:
        copy(root, output / root.name)
    if not packaged.is_dir():
        (output / "scripts").mkdir()
        copy(checkout / "scripts" / "bootstrap-ubuntu.sh", output / "scripts" / "bootstrap-ubuntu.sh")
    return {"version": __version__, "output": str(output), "files": count,
            "setup_skill": str(output / "skills" / "setup-genlayer-agent-lab" / "SKILL.md"),
            "setup_page": str(output / "docs" / "START.html")}
