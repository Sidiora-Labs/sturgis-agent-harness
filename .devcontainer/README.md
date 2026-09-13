# Development container

Open this checkout with VS Code's **Dev Containers: Reopen in Container** command.
The image provides Python 3.12, Node 22.22.2, a C++20 compiler, CMake, SQLite headers,
bubblewrap, resource-limit tools and the locked Playwright system libraries.
`post-create.sh` installs the editable project, developer/test/documentation
dependencies, both locked npm dependency trees and Chromium as the non-root
`sturgis` user. It does not start services or execute the full test suites.

After setup:

```sh
make build
make dev
```

Open the forwarded Sturgis port at `http://127.0.0.1:8787`. In separate terminals,
`make dev-web`, `make dev-docs-users` and `make dev-docs-developers` start the web
and manual development servers. `make lint typecheck` runs static checks;
`make qualify` and `make docs-check` run the full product and documentation gates.

The default is trusted local `self_hosted` mode with a loopback listener. The
container contains no API keys, passwords or bootstrap credentials. Configure
your own connections through the local application. Keep forwarded ports private;
use the configured loopback origin when developing locally. If an environment
assigns a different external URL, explicitly configure the matching public origin
before starting the service. Do not publish development ports as a hosted service.
The standalone gateway is optional and does not start automatically; use its CLI
when needed. Port forwarding does not itself start any server.

The checkout is the standard development workspace mount. There are no host Docker
socket mounts, privileged mode, added capabilities or disabled seccomp settings.
VS Code can adjust the developer UID/GID to match Linux workspace ownership.
Project build output remains in the checkout; private runtime data uses the
application's configured data directory. Preserve or back up that directory before
rebuilding a container if you need its development data. Container removal can
discard data outside the mounted workspace.

Hosted workers and isolated browser tools create additional Linux namespaces.
Docker Desktop, Codespaces or another outer host may disallow those operations.
The default container therefore does **not** guarantee that hosted-mode or managed
browser qualification will run. Check with
`bash .github/scripts/check-isolation.sh`; a denied check is a real environment
limitation, not a reason to bypass the runtime's isolation. Use a Linux development
VM with the required namespace policy for those checks.

This is a development environment for an actively changing project, not a hardened
deployment image or a production-readiness claim. See [CONTRIBUTING.md](../CONTRIBUTING.md)
and [the CI notes](../.github/ci/README.md) for the complete workflow.
