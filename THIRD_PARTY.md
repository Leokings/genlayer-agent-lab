# Third-party components

This project contains fresh application code and depends on separately licensed open-source packages. It does not copy code from the other applications in the parent workspace.

- GenLayer testing suite: https://github.com/genlayerlabs/genlayer-testing-suite — MIT, Copyright (c) 2024 GenLayer Labs.
- Optional GenLayer Studio: https://github.com/genlayerlabs/genlayer-studio — MIT, Copyright (c) 2024 GenLayer Labs Corp. Fetched separately at the pinned commit. The generated image retains its license at `/app/GENLAYER_STUDIO_LICENSE`; its source is not bundled in the Lab wheel.
- GenVM runner artifacts: https://github.com/genlayerlabs/genvm — downloaded into a local cache, with version and SHA256 checked. See the upstream release and licenses for runtime components.
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk — MIT.
- FastAPI: https://github.com/fastapi/fastapi — MIT.
- SQLite: https://www.sqlite.org/copyright.html — public domain.

The Windows loader compatibility wrapper is scoped to the private worker and follows the upstream loader's message encoding interface; upstream package files are not rewritten. Retain dependency copyright and license notices in redistributions. `uv.lock` records the complete installed dependency set and distribution hashes.
