#!/usr/bin/env bash
# Ubuntu prerequisite bootstrap. Run as the normal Lab installation user.
# No Python, Git, uv, or Docker is needed to run this Bash script.
set -u
set -o pipefail

usage() {
    cat <<'HELP'
Usage: bash bootstrap-ubuntu.sh [--check | --install [--interactive]]

Default/--check: inspect prerequisites without downloads, sudo, or changes.
--install: install missing prerequisites; privileged commands use sudo -n.
--interactive: with --install only, allow sudo to ask you for a password.

Supported: Ubuntu 22.04/24.04 on x86-64, as a normal (non-root) user.
Checks Git, curl, CA certificates, tmux, uv, local Linux Docker and Compose >=2.
Uses Ubuntu apt, Docker's official apt repository, and Astral's uv installer.
Does not install the Lab or an agent, change Docker contexts, remove packages
or data, reboot, expose ports, or alter agent/model settings.

Exit codes:
  0  All prerequisites are usable by this user.
  2  Check found missing/unready prerequisites, or a service needs manual start.
  3  Unsupported host, conflicting Docker installation, or nonlocal context.
  4  sudo is missing or permission is unavailable; interactive/admin action needed.
  5  A bounded install, download, or service operation failed; inspect and retry.
  6  Docker group access needs a new login; reconnect, then rerun --check.
 64  Invalid command-line arguments.
HELP
}

mode=check
mode_selected=false
interactive=false
for argument in "$@"; do
    case "$argument" in
        --check|--install)
            if $mode_selected; then usage >&2; exit 64; fi
            mode=${argument#--}
            mode_selected=true
            ;;
        --interactive) interactive=true ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 64 ;;
    esac
done
if $interactive && [[ "$mode" != install ]]; then usage >&2; exit 64; fi

say() { printf '%s\n' "$*"; }
fail() { local code=$1; shift; say "$*" >&2; exit "$code"; }
bounded() { timeout --signal=TERM --kill-after=10 "$@"; }
tool_works() { command -v "$1" >/dev/null 2>&1 && bounded 10 "$@" >/dev/null 2>&1; }
package_installed() {
    [[ "$(bounded 10 dpkg-query -W -f='${Status}' "$1" 2>/dev/null)" == 'install ok installed' ]]
}

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    fail 3 'Supported host required: Ubuntu 22.04/24.04 Linux x86-64. No changes made.'
fi
os_id= os_version=
os_release=$(cat /etc/os-release 2>/dev/null) || fail 3 'Cannot identify this Ubuntu host. No changes made.'
while IFS='=' read -r name value; do
    value=${value#\"}; value=${value%\"}
    case "$name" in ID) os_id=$value ;; VERSION_ID) os_version=$value ;; esac
done <<< "$os_release"
if [[ "$os_id" != ubuntu || ( "$os_version" != 22.04 && "$os_version" != 24.04 ) ]]; then
    fail 3 'Supported host required: Ubuntu 22.04 or 24.04. No changes made.'
fi
[[ "$(id -u)" != 0 ]] || fail 3 'Run this helper as your normal installation user, without sudo before bash.'
install_user=$(id -un) || fail 3 'Cannot identify the normal installation user.'
for command in timeout apt-get dpkg-query; do
    command -v "$command" >/dev/null 2>&1 || fail 3 "Required Ubuntu base tool is missing: $command."
done

# A previous uv install may only need its user-local PATH loaded. This changes
# this helper's environment, never shell startup files or a global installation.
export PATH="$HOME/.local/bin:$PATH"
missing_packages=()
tool_works git --version || missing_packages+=(git)
tool_works curl --version || missing_packages+=(curl)
package_installed ca-certificates || missing_packages+=(ca-certificates)
tool_works tmux -V || missing_packages+=(tmux)
uv_ready=false
tool_works uv --version && uv_ready=true
docker_present=false
tool_works docker --version && docker_present=true
compose_ready=false
docker_ready=false
docker_endpoint=unix:///var/run/docker.sock

docker_call() {
    # Pin only the already-selected local endpoint. Do not change user context.
    bounded 15 env -u DOCKER_HOST -u DOCKER_CONTEXT docker --host "$docker_endpoint" "$@"
}
validate_engine() {
    [[ "$1" == 'linux x86_64' || "$1" == 'linux amd64' ]]
}
inspect_docker() {
    local info version
    if info=$(docker_call info --format '{{.OSType}} {{.Architecture}}' 2>/dev/null); then
        validate_engine "$info" || fail 3 'The selected Docker engine must run Linux x86-64 containers.'
        docker_ready=true
    fi
    version=$(docker_call compose version --short 2>/dev/null) || version=
    if [[ "$version" =~ ^v?([0-9]+)\. ]] && (( ${BASH_REMATCH[1]} >= 2 )); then
        compose_ready=true
    fi
}

resolve_docker_endpoint() {
    if [[ -n "${DOCKER_CONTEXT:-}" ]]; then
        docker_endpoint=$(bounded 10 docker context inspect "$DOCKER_CONTEXT" --format '{{.Endpoints.docker.Host}}' 2>/dev/null) \
            || fail 3 'Cannot inspect the selected Docker context; inspect it yourself before retrying.'
    elif [[ -n "${DOCKER_HOST:-}" ]]; then
        docker_endpoint=$DOCKER_HOST
    else
        docker_endpoint=$(bounded 10 docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null) \
            || fail 3 'Cannot inspect the selected Docker context; inspect it yourself before retrying.'
    fi
    [[ "$docker_endpoint" == unix:///* ]] \
        || fail 3 'A local Unix-socket Docker context is required. This helper will not switch contexts.'
}
if $docker_present; then
    resolve_docker_endpoint
    inspect_docker
elif [[ -n "${DOCKER_HOST:-}${DOCKER_CONTEXT:-}" ]]; then
    fail 3 'Docker environment overrides are set but its CLI is unavailable. Resolve that configuration before retrying.'
fi

# Healthy existing engines/plugins are reused, including Ubuntu-packaged Docker.
# Installing official packages into another Docker distribution needs a deliberate
# administrator decision; this helper never removes or replaces that distribution.
docker_install=false
missing_official_engine=false
if $docker_present && ! $docker_ready && [[ "$docker_endpoint" == unix:///var/run/docker.sock || "$docker_endpoint" == unix:///run/docker.sock ]] \
    && package_installed docker-ce-cli; then
    if ! package_installed docker-ce || ! package_installed containerd.io; then
        missing_official_engine=true
    fi
fi
if ! $docker_present || ! $compose_ready || $missing_official_engine; then
    if $docker_present && ! package_installed docker-ce-cli; then
        fail 3 'Existing Docker lacks usable Compose >=2 and is not the official Docker CLI package. Add its compatible Compose plugin through that installation, then rerun this helper.'
    fi
    for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc docker-desktop moby-engine moby-cli; do
        if package_installed "$package"; then
            fail 3 "Conflicting Docker package: $package. Ask your administrator to choose a compatible Docker/Compose installation; nothing will be removed."
        fi
    done
    if command -v snap >/dev/null 2>&1 && bounded 10 snap list docker >/dev/null 2>&1; then
        fail 3 'A Docker Snap installation is present. Ask its administrator to provide compatible Docker/Compose access; this helper will not replace it.'
    fi
    docker_install=true
fi

if [[ "$mode" == check ]]; then
    ((${#missing_packages[@]} == 0)) || say "Missing Ubuntu prerequisites: ${missing_packages[*]}"
    $uv_ready || say 'Missing or unusable uv in this user environment.'
    $docker_ready || say 'Docker is not usable by this user; the local engine may need starting or user access.'
    $compose_ready || say 'Docker Compose >=2 is missing or unavailable.'
    if ((${#missing_packages[@]} > 0)) || ! $uv_ready || ! $docker_ready || ! $compose_ready; then
        say 'No changes made. To install missing prerequisites, rerun this helper with --install.'
        exit 2
    fi
    say 'Prerequisites ready. Load ~/.local/bin into your shell PATH if needed; continue with Lab installation.'
    exit 0
fi

sudo_flags=(-n)
$interactive && sudo_flags=()
privilege_checked=false
require_privilege() {
    $privilege_checked && return
    local permission=false
    if command -v sudo >/dev/null 2>&1; then
        if $interactive; then
            sudo -v && permission=true
        else
            bounded 20 sudo -n -v && permission=true
        fi
    fi
    if ! $permission; then
        say 'Administrator permission is needed for missing system prerequisites.' >&2
        say 'In your own interactive terminal, run: bash bootstrap-ubuntu.sh --install --interactive' >&2
        fail 4 'Use the same helper path. If sudo is unavailable or your account is not permitted, ask the server administrator to install the listed prerequisites.'
    fi
    privilege_checked=true
}
root_command() {
    local seconds=$1; shift
    require_privilege
    sudo "${sudo_flags[@]}" timeout --signal=TERM --kill-after=10 "$seconds" "$@"
}
apt_command() {
    root_command 600 env DEBIAN_FRONTEND=noninteractive apt-get \
        -o Acquire::Retries=2 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 \
        -o DPkg::Lock::Timeout=30 "$@"
}
apt_updated=false
update_apt() {
    $apt_updated && return
    say 'Updating Ubuntu package indexes (bounded to 10 minutes)...'
    apt_command update || fail 5 'Package-index update failed. Resolve the reported apt error, then rerun --install.'
    apt_updated=true
}
if ((${#missing_packages[@]} > 0)); then
    require_privilege
    update_apt
    say "Installing missing Ubuntu prerequisites: ${missing_packages[*]}"
    apt_command install -y --no-remove "${missing_packages[@]}" \
        || fail 5 'Prerequisite package installation failed. Inspect the apt error and rerun --install.'
fi

bootstrap_temp=
cleanup() {
    if [[ -n "$bootstrap_temp" ]]; then
        rm -f -- "$bootstrap_temp/uv-install.sh" "$bootstrap_temp/docker.asc" "$bootstrap_temp/docker.sources"
        rmdir -- "$bootstrap_temp" 2>/dev/null || true
    fi
}
trap cleanup EXIT
make_temp() {
    [[ -n "$bootstrap_temp" ]] && return
    bootstrap_temp=$(mktemp -d -t genlayer-prereqs.XXXXXX) || fail 5 'Cannot create a private download directory.'
}
download() {
    bounded 200 curl --fail --location --proto '=https' --tlsv1.2 \
        --connect-timeout 10 --max-time 120 --retry 2 --retry-max-time 180 --output "$2" "$1" \
        || fail 5 'Official prerequisite download failed within its time limit. Check the reported network error, then rerun --install.'
}
official_repository_present() {
    # Read only active apt source formats, not comments or *.save backups. awk
    # reads missing glob matches harmlessly, so a fresh host needs no source file.
    local suite=noble
    [[ "$os_version" == 22.04 ]] && suite=jammy
    bounded 10 awk -v expected_suite="$suite" '
        function record(s,    n, a, i, key, value, types, uri, enabled, suite, components) {
            n=split(s,a,"\n"); enabled="yes"
            for (i=1;i<=n;i++) {
                key=a[i]; sub(/:.*/,"",key); value=a[i]; sub(/^[^:]*:[ \t]*/,"",value)
                if (key=="Types") types=value
                if (key=="URIs") uri=value
                if (key=="Enabled") enabled=tolower(value)
                if (key=="Suites") suite=value
                if (key=="Components") components=value
            }
            return enabled!="no" && types ~ /(^|[ \t])deb([ \t]|$)/ && uri ~ /(^|[ \t])https:\/\/download\.docker\.com\/linux\/ubuntu\/?([ \t]|$)/ && suite ~ ("(^|[ \t])" expected_suite "([ \t]|$)") && components ~ /(^|[ \t])stable([ \t]|$)/
        }
        BEGIN {
            found=0
            for (i=1;i<ARGC;i++) {
                block=""
                while ((getline line < ARGV[i])>0) {
                    sub(/\r$/,"",line)
                    if (ARGV[i] ~ /\.sources$/) {
                        if (line ~ /^[ \t]*$/) { if (record(block)) found=1; block="" }
                        else if (line !~ /^[ \t]*#/) block=block line "\n"
                    } else if (line ~ ("^[ \t]*deb[ \t]+(\\[[^]]*\\][ \t]+)?https://download\\.docker\\.com/linux/ubuntu/?[ \t]+" expected_suite "[ \t]+stable([ \t]|$)")) found=1
                }
                if (record(block)) found=1
                close(ARGV[i])
            }
            exit !found
        }' /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources
}
add_repository_file() {
    local result
    # Reuse only exact content from the official download/generated source. This
    # also resumes a previous attempt interrupted between the two file writes.
    root_command 15 sh -c 'if [ -L "$2" ]; then exit 3; fi
        if [ -e "$2" ]; then cmp -s "$1" "$2" || exit 3
        else (set -C; umask 022; cat "$1" > "$2") || exit 5; fi' sh "$1" "$2"
    result=$?
    [[ "$result" == 0 ]] && return
    [[ "$result" != 3 ]] || fail 3 "Existing Docker repository file differs or is a symlink: $2. Ask the administrator to inspect it; this helper will not overwrite it."
    fail 5 "Cannot add Docker repository file: $2. Resolve the reported error, then rerun --install."
}
if $docker_install; then
    require_privilege
    if ! official_repository_present; then
        key_path=/etc/apt/keyrings/genlayer-agent-lab-docker.asc
        source_path=/etc/apt/sources.list.d/genlayer-agent-lab-docker.sources
        make_temp
        download https://download.docker.com/linux/ubuntu/gpg "$bootstrap_temp/docker.asc"
        suite=noble
        [[ "$os_version" == 22.04 ]] && suite=jammy
        printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: amd64\nSigned-By: %s\n' \
            "$suite" "$key_path" > "$bootstrap_temp/docker.sources"
        root_command 15 install -d -m 0755 /etc/apt/keyrings || fail 5 'Cannot prepare the apt key directory.'
        add_repository_file "$bootstrap_temp/docker.asc" "$key_path"
        add_repository_file "$bootstrap_temp/docker.sources" "$source_path"
    fi
    apt_updated=false
    update_apt
    docker_packages=()
    required_docker_packages=(docker-ce-cli docker-buildx-plugin docker-compose-plugin)
    if [[ "$docker_endpoint" == unix:///var/run/docker.sock || "$docker_endpoint" == unix:///run/docker.sock ]]; then
        required_docker_packages+=(docker-ce containerd.io)
    fi
    for package in "${required_docker_packages[@]}"; do
        package_installed "$package" || docker_packages+=("$package")
    done
    if ((${#docker_packages[@]} > 0)); then
        say 'Installing missing official Docker Engine/Compose components...'
        apt_command install -y --no-remove "${docker_packages[@]}" \
            || fail 5 'Docker package installation failed. Inspect the apt error, then rerun --install.'
    fi
fi

if ! $uv_ready; then
    make_temp
    download https://astral.sh/uv/install.sh "$bootstrap_temp/uv-install.sh"
    say 'Installing uv in the normal user environment (bounded to 5 minutes)...'
    bounded 300 env -u UV_UNMANAGED_INSTALL UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$bootstrap_temp/uv-install.sh" \
        || fail 5 'User-local uv installation failed. Inspect its output, then rerun --install.'
fi

docker_ready=false
compose_ready=false
# A newly installed CLI can reveal a previously saved context. Resolve it again
# before readiness, startup, or group changes; never silently use the default.
resolve_docker_endpoint
inspect_docker
needs_login=false
if ! $docker_ready; then
    if [[ "$docker_endpoint" != unix:///var/run/docker.sock && "$docker_endpoint" != unix:///run/docker.sock ]]; then
        fail 2 'Start the service that owns your selected local Docker socket, then rerun --check. This helper will not substitute another engine or context.'
    fi
    require_privilege
    root_info=$(root_command 20 env -u DOCKER_HOST -u DOCKER_CONTEXT docker --host "$docker_endpoint" info --format '{{.OSType}} {{.Architecture}}' 2>/dev/null) || root_info=
    if [[ -z "$root_info" ]]; then
        bounded 10 systemctl cat docker.service >/dev/null 2>&1 \
            || fail 2 'No managed Docker service was found. Start your existing local engine, then rerun --check.'
        say 'Starting the existing local Docker service...'
        root_command 60 systemctl start docker.service \
            || fail 5 'Docker service startup failed. Inspect systemctl status docker.service, then rerun --install.'
        root_info=$(root_command 20 env -u DOCKER_HOST -u DOCKER_CONTEXT docker --host "$docker_endpoint" info --format '{{.OSType}} {{.Architecture}}' 2>/dev/null) \
            || fail 5 'Docker is still unavailable after service startup. Inspect its service diagnostics before retrying.'
    fi
    validate_engine "$root_info" || fail 3 'The selected Docker engine must run Linux x86-64 containers.'
    # Starting the service may have been sufficient; do not change groups unless
    # the normal user still cannot access this exact local engine.
    docker_ready=false
    inspect_docker
    if ! $docker_ready; then
        if [[ " $(id -nG) " == *' docker '* ]]; then
            fail 5 'Docker works for the administrator but remains unavailable with your active docker group. Ask the administrator to inspect socket access; no permissions were loosened.'
        fi
        if [[ " $(id -nG "$install_user") " != *' docker '* ]]; then
            if ! bounded 10 getent group docker >/dev/null 2>&1; then
                root_command 15 groupadd --system docker || fail 5 'Cannot create the Docker access group.'
            fi
            root_command 15 usermod -aG docker "$install_user" || fail 5 'Cannot grant this installation user Docker access.'
        fi
        needs_login=true
    fi
fi

# A successful installer invocation is not proof that its prerequisites work.
for specification in 'git --version' 'curl --version' 'tmux -V' 'uv --version'; do
    read -r command argument <<< "$specification"
    tool_works "$command" "$argument" || fail 5 "Prerequisite remains unavailable after installation: $command."
done
package_installed ca-certificates || fail 5 'CA certificates remain unavailable after installation.'
$compose_ready || fail 5 'Docker Compose >=2 remains unavailable after installation.'
if $needs_login; then
    say 'Docker group access is configured but this login has not picked it up.'
    say 'Log out of this SSH session completely, reconnect as the same user, then rerun: bash bootstrap-ubuntu.sh --check'
    say 'Use the same helper path. Start a new tmux session after reconnecting; an existing session retains its old groups.'
    say 'The owning agent/Gateway process must also restart with refreshed groups. An existing systemd user service manager may retain old groups too; have its owner/administrator refresh that manager after saving work, then restart the agent.'
    say 'Verify docker info from the actual agent session before it continues Lab setup; restarting only the Gateway may not refresh its parent manager.'
    say 'Existing images, volumes, contexts, and agent settings are preserved. A reboot is unnecessary.'
    exit 6
fi
$docker_ready || fail 5 'Docker is still unavailable to the installation user.'
say 'Prerequisites ready. Continue Lab installation as this normal user.'
say 'If uv is missing in your parent shell, run: export PATH="$HOME/.local/bin:$PATH"'
say 'This helper has not started the Lab or configured your agent connection.'
