# Verifying a distributable installation

The release gate installs the built wheel into a new virtual environment and runs from a new working directory outside the checkout. It does not treat tests against an editable source installation as evidence that a wheel works.

```sh
uv build --out-dir dist-ci
uv run python -I scripts/verify-install.py \
  --wheel 'dist-ci/genlayer_agent_lab-*-py3-none-any.whl' \
  --require-kit \
  --output .lab/install-verification.json
```

The quoted wheel glob must select exactly one artifact. Use a separate output directory for each build rather than accidentally testing an older wheel. `--require-kit` requires the packaged integration kit introduced in alpha 4. Omit it only when checking an earlier artifact. `--backend glsim` additionally runs all 18 scenarios through the trusted bundled contract; the default uses 18 fixture cases plus the doctor's actual GLSim execution.

The script performs these checks:

1. Creates a fresh virtual environment, home directory, cache and application state under an owned system temporary directory. Installs the wheel and its declared dependencies from the public Python package index with pip's cache disabled.
2. Copies only the verification script into that temporary directory and runs it with the installed interpreter's `-I` isolation. Confirms the imported package comes from the fresh environment and that distribution metadata matches its version.
3. Reads and hashes required packaged assets, the bundled intelligent contract, the Dockerfile, dependency lock and Studio relay. Checks the installed console entry point, initializes fresh state, and runs `doctor`. The doctor must execute the actual pinned trusted contract successfully.
4. Requires all 18 scenario cases to pass all four grades. With `--require-kit`, exports the packaged skill, Python/MCP/TypeScript clients and installation/service/recovery documentation into a new directory and requires those files to be nonempty.
5. Starts a separate installed HTTP service on an ephemeral loopback port. Requires authentication, verifies dashboard resources, completes a run through the installed Python HTTP client, and checks the server's persisted grades.
6. Completes a second run through an actual MCP stdio subprocess and verifies the five run-scoped tools. Neither the agent's own claimed success nor a successful tool call replaces the server-side grades.

Child processes receive an explicit environment allowlist. Host model/wallet/cloud credentials, `PYTHONPATH`, `PYTHONHOME`, `VIRTUAL_ENV`, Lab connection settings, proxy variables and package-index credentials are not inherited. HTTP/MCP tokens are freshly generated for the temporary installation and never included in the JSON report. The verifier stops only its own service and removes only its own temporary directory.

Initial installation needs internet access to download declared Python dependencies and the pinned GenVM bundle. This exercises a clean cache; it does not call a paid model provider or a public chain. The fixture suite and trusted GLSim doctor do not require Docker or Studio.

## CI and local evidence

`.github/workflows/ci.yml` defines Ubuntu, macOS and Windows jobs that build the artifact, perform this clean installation check, and upload its sanitized JSON report. The separate Ubuntu custom-contract Docker gate is retained. A workflow definition is an authored gate; a green GitHub Actions run and its artifact are the evidence that the corresponding runner actually passed it.

A local Linux Docker check can install the same wheel inside a fresh official Python Linux image. Mount only the wheel read-only, pass the verification script over stdin, and provide no host virtual environment, caches, credentials, project source or Docker socket. A temporary filesystem containing the fresh virtual environment needs explicit `exec` permission so its installed console scripts and Python extensions can run; the container root can remain read-only. Retrieve the sanitized report from that owned container and remove only that container afterward. This establishes Linux userspace behavior inside Docker on the current host; it is not evidence of a native Linux or macOS runner.

The JSON report records the wheel SHA-256, Python/platform identity, installed distribution versions, resource hashes, doctor evidence, suite counts and HTTP/MCP results. It distinguishes the fixture scenario suite from actual trusted GLSim execution. Studio consensus, appeals, custom contracts and remote operating-system execution retain their separate verification records.
