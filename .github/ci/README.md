# Development automation

Sturgis is under active development. These workflows check a specific source
revision; a passing workflow is not a production-readiness or deployment claim.

The [CI workflow](../workflows/ci.yml) runs three independent jobs on pushes and
pull requests. View its runs in
[GitHub Actions](https://github.com/Sidiora-Labs/sturgis-agent-harness/actions):

| Job                   | Local equivalent                   | Scope                                                                                                                                                                                              |
| --------------------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Quality               | `make lint typecheck format-check` | Python and JavaScript linting, strict frontend types and formatting; shell scripts also pass ShellCheck.                                                                                           |
| Product qualification | `make qualify`                     | The complete existing gateway qualifier: Python, C++, client components and actual local browser journeys, with separate 90% line floors for Python, gateway Python, C++ and authored client code. |
| Documentation         | `make build docs-check`            | Both documentation sites, source/feature completeness, examples, links, browser journeys and documentation-tool coverage floors.                                                                   |

Jobs install Python 3.12 and the Node version declared in `.node-version`, use both
npm lockfiles, and install native compilation, SQLite, bubblewrap and resource-limit
dependencies. Product and documentation jobs install the locked Playwright Chromium
build. Job failures remain failures; artifact collection only preserves available
reports. CI neither publishes packages nor deploys services and requires no vendor
credentials. Historical receipts are not substituted for executing the gates.

The Ubuntu 22.04 VM runner is explicit because the complete product suite requires
Linux namespaces. `.github/scripts/check-isolation.sh` checks those operations as
the runner user. Installing bubblewrap alone does not establish permission to use
it: an outer container, AppArmor or seccomp may still deny user/network namespaces.
A denied preflight fails the product job. Do not remove isolation, enable privileged
containers, or skip tests to make it pass. Use an appropriately administered Linux
VM. Never expose a persistent self-hosted runner with sensitive access to untrusted
pull requests. The default workflow uses disposable GitHub-hosted runners.

All external Actions are pinned to full commits verified against their official
release tags. Dependabot proposes updates for review; it does not merge them:

- [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1)
- [setup-python v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0)
- [setup-node v7.0.0](https://github.com/actions/setup-node/releases/tag/v7.0.0)
- [upload-artifact v7.0.1](https://github.com/actions/upload-artifact/releases/tag/v7.0.1)

These Actions require a runner supporting Node 24 actions. The application itself
uses the separately selected Node 22 toolchain. The workflow targets GitHub.com;
the artifact action is not a GitHub Enterprise Server compatibility claim.

There is no CODEOWNERS file until actual repository maintainers are assigned.
