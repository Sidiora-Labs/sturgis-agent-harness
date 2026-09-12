# Install a release

Build on the Linux architecture and CPython version used by the installation:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]' setuptools wheel
npm ci
bash packaging/build-wheel.sh .build/release
python3.12 -m venv /opt/sturgis
/opt/sturgis/bin/python -m pip install .build/release/sturgis_core-*.whl
/opt/sturgis/bin/sturgis setup
/opt/sturgis/bin/sturgis serve
```

The platform wheel contains the Python CLI, consumer services, built web assets
and fonts, and the release C++ memory executable. Python package dependencies are
declared in wheel metadata. Linux must provide the SQLite runtime library. Node.js,
the compiler and source checkout are build dependencies only. No vendor account is
needed to install or run local content services; model work requires a configured
and successfully probed provider. The bootstrap token is written privately under
the operator's data root and is entered once when creating the first account.

The build script normally runs the web production build. A qualification job that
has already built and hashed those assets may set `STURGIS_USE_BUILT_WEB=1` to
package that exact output. The installation test verifies every packaged web file
against its input hash and exercises both worker modes through the installed CLI.

`sturgis.service` is an operator example for an installation at `/opt/sturgis`.
Create the dedicated OS account before enabling it and set the real public origin
when placing the service behind an HTTPS reverse proxy. Keep the data root private.
The control service's session and credential material is outside agent workspaces.

# Hosted workers

Set `STURGIS_MODE=hosted`. Install bubblewrap and util-linux (`prlimit`) and permit
unprivileged user, mount, PID, IPC and UTS namespaces for the service identity. The
worker fails closed when these facilities are unavailable. Each agent gets its own
writable runtime/workspace/private roots; source, dependencies and system libraries
are read-only. Other agents and the control root are not mounted. The network is
shared and the HTTP tools enforce their configured origin policy. This does not
provide host network isolation or a hostile-kernel security boundary.

The default hard per-process virtual address-space ceiling is 2 GiB, with an
operator setting from 128 MiB to 256 GiB. This is an address-space limit, not an
aggregate physical-memory quota. Managed Chromium needs large virtual reservations;
the measured self-hosted renderer works under its separate 256 GiB ceiling. On the
qualification host the zero-capability hosted worker cannot initialize Chromium's
nested network namespace, and browser readiness explicitly reports unavailable.
No worker capabilities or network restrictions are relaxed to bypass that result.

`Dockerfile` is a container packaging example, not an exercised deployment. Build
the wheel against the image's matching architecture, CPython and libc. Copy its
actual filename into the Docker build context and pass `STURGIS_WHEEL` as a build
argument. Set the public origin and persist the data volume. Nested hosted workers
additionally depend on the outer container's namespace/security policy; this image
does not request privileged execution or silently fall back to process mode.

# Backup contract

The authenticated API exports `sturgis-backup/1` ZIP archives with SHA-256 manifest
entries: at most 4 MiB compressed, 32 MiB expanded, 8 MiB per file and 512 files.
Export holds lifecycle admission ownership through the stopped-worker snapshot.
Database contents are logical JSON, never copied live SQLite/WAL files. Restore
rejects paths, links, schemas, hashes and size violations before publishing a new
owned agent. The installation assigns all target paths and regenerates profile
resources; an archive cannot select an absolute filesystem root.

Workspace files, attachments, knowledge revisions, canonical memory revisions,
current derived memory, retractions and labeled conversation history survive.
Exact historical JSON remains available through immutable artifact references.
Historical event payloads remain exact; their imported source has no observer
authority. Past generation metadata is retained as history, while current memory
is rebuilt and validated through the native protocol. Imported input is placed
behind both ingestion cursors and cannot restart old work.

Restore reserves a durable, non-runnable target before writing files or memory.
Cancellation waits for local writes to settle before removing that unpublished
target. If the control process crashes, startup discards only targets still marked
`restoring`, before lifecycle admission starts; existing published agents remain.

Vault bytes, IPC keys and credentials are excluded. Provider/connection
configuration, activation and grant history remains inert in the journal
snapshot; it does not restore live authority. Execution grants, pending approvals,
schedules, workflows, children and plugin activation are not restored. Skills arrive disabled. Reconnect and explicitly
enable the desired capabilities after restore. The backup can contain secrets a
user deliberately placed in ordinary workspace files or conversation text, so
store it as private user data.
