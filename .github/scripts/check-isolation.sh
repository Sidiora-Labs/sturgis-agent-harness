#!/usr/bin/env bash
# Check the actual namespace operations without changing host security policy.
set -euo pipefail

for required_tool in bwrap prlimit; do
    if ! command -v "$required_tool" >/dev/null 2>&1; then
        printf 'Missing required Linux tool: %s\n' "$required_tool" >&2
        exit 1
    fi
done

isolation_mounts=()
for system_path in /usr /lib /lib64 /bin; do
    if [[ -e "$system_path" ]]; then
        isolation_mounts+=(--ro-bind "$system_path" "$system_path")
    fi
done

if ! prlimit --cpu=10 --nofile=256 -- bwrap \
    --unshare-all --die-with-parent --new-session --clearenv --cap-drop ALL \
    "${isolation_mounts[@]}" --proc /proc --dev /dev --tmpfs /tmp -- /bin/true; then
    cat >&2 <<'MESSAGE'
The runner denied Linux namespace isolation required by the complete suite.
Sturgis needs unprivileged user, mount, PID, IPC, UTS and network namespaces.
Use a Linux VM whose administrator permits these operations. Container hosts,
AppArmor and seccomp can deny them even when bubblewrap is installed.
This check does not disable security policy, switch worker mode, or skip tests.
MESSAGE
    exit 1
fi
printf 'Required Linux namespace and resource-limit operations are available.\n'
