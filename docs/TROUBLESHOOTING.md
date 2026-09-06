# Runtime troubleshooting

## Local Studio startup

Use `gl-agent-lab --data-dir YOUR_LAB studio status`. A healthy Docker container
alone is insufficient: status also checks actual image identity, owned volumes,
network membership, published ports and RPC compatibility. A
`verification_failure` names a failed configuration check without exposing
provider settings. `studio up` regenerates its configuration from this
installation's metadata and preserves its database/cache.

Three Windows/Docker integration issues are handled by the installer:

- `MappingProxyType` import failure during migration: the upstream image's
  empty Python-path entry exposes a local `types.py`. The generated service
  explicitly sets `PYTHONPATH=/app`.
- `/entrypoint.sh: No such file or directory` when the file exists: Windows Git
  newline conversion can change a Linux shebang. Source archives are generated
  with command-scoped `core.autocrlf=false`; global Git settings are unchanged.
- A requested port with no actual published binding: some Docker engines do
  not publish ports on internal networks. A fixed-destination relay publishes
  the loopback port, while the core services remain on the internal network.
  Its root POST route maps to Studio's `/api` endpoint.

GenVM preparation can take several minutes on first startup. Subsequent starts
reuse its owned precompile marker. A stopped Studio stack is restored with
`studio up`; no factory reset is required. Keep the unauthenticated RPC local.

Use `uv run gl-agent-lab doctor` for the bundled native runtime and `uv run gl-agent-lab worker doctor` for custom-contract Docker execution. The latter distinguishes a missing engine from a missing worker image. After the engine is available, `worker build` creates the image and executes a contract readiness probe.

## Windows Docker startup: inaccessible local socket

On this development machine, Docker Desktop 4.58.0 failed before its Linux API appeared. Its backend log named `dockerInference`, then `docker-secrets-engine/engine.sock`, with Windows error 1920: the file could not be accessed. The objects were stale AF_UNIX runtime endpoints. WSL itself started successfully.

The user performed a factory reset, but those socket objects remained. With Docker stopped, moving the affected runtime directories to uniquely named sibling quarantine directories allowed Docker to recreate them and start its Linux engine. The affected paths were `%LOCALAPPDATA%\Docker\run` and `%LOCALAPPDATA%\docker-secrets-engine`. The latter contained only the stale socket on this machine. No project database was reset.

Treat this as a diagnosis-specific repair. Inspect the exact current error and directory metadata first; another installation can contain different data. Stop Docker before moving runtime files, verify the full source/destination paths, and preserve the quarantined directories until startup succeeds. Do not recursively delete a broad application-data directory or reset unrelated WSL distributions. A factory reset deletes Docker data and requires the installation owner's authorization.

Similar startup failures are reported in Docker's [issue tracker](https://github.com/docker/desktop-feedback/issues/625). Docker documents its [supported troubleshooting and log locations](https://docs.docker.com/desktop/troubleshoot-and-support/troubleshoot/). A matching error message is evidence to investigate, not proof every host has the same cause.

## Engine available, image missing

`docker_available: true` with `image_ready: false` means the engine probe succeeded. Run `uv run gl-agent-lab worker build`. This downloads the pinned base image and locked dependencies during the build; execution runs without network access. The image tag follows the installed toolkit version. A missing or incompatible image never triggers native execution of custom source.

## Inconclusive custom run

An inconclusive result is an infrastructure/execution failure rather than an agent grade. Check worker readiness, runner header, public write method, argument count, fixture pattern and result path. Unmatched model/web calls fail in the offline worker. Run the bundled delivery example to distinguish an installation problem from a contract-specific binding problem.

The four grades remain separate: successful execution can produce a failed decision grade if the returned verdict disagrees with the scenario's independent expectation. Inspect the report's runtime diagnostics and scenario snapshot together.
