---
title: "Set up a development checkout"
description: "Install the actual build dependencies and run the consumer service without inventing a second runtime."
---

The supported execution backend is Linux with Python 3.12 or newer, a C++20 compiler, CMake and SQLite development headers. Native coverage additionally uses GCC/gcov and gcovr. Hosted workers, plugin execution and the process tool need bubblewrap, util-linux `prlimit`, and permission to create the required Linux namespaces. Missing isolation support is an explicit failure.

From the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]' setuptools wheel
npm ci
bash packaging/build-wheel.sh .build/release
```

The wheel script builds web assets and a separate release native executable. It does not replace the instrumented native build used by qualification. To prepare the tested development native path, use the documented native verification entry point:

```sh
.venv/bin/python scripts/verify_native.py
.venv/bin/sturgis setup --root /absolute/private/sturgis-data
.venv/bin/sturgis serve
```

The listener defaults to `127.0.0.1:8787`. The CLI configuration defaults beneath the XDG configuration directory; agent data belongs outside the source checkout and virtual environment. The first service startup creates a private bootstrap token when no token was supplied. Use the browser bootstrap form or `sturgis login --bootstrap --email you@example.com`; passwords and bootstrap tokens are interactive secrets, not command-line arguments.

## First useful verification

Create an owner and an agent, upload a text file, and inspect it before configuring a model. These paths exercise authentication, the control-to-worker boundary, content storage and the native process without a paid provider. Open Model Gateway, choose a provider, supply its key or sign in, and import its live models. In agent Settings, choose Use model; the consumer boundary installs a restricted gateway credential, probes capabilities, and activates the model automatically. The standalone gateway can also be exercised without creating any agent; see [gateway architecture](../gateway/architecture.md).

The self-hosted gateway permits an explicitly chosen loopback model server by default; `sturgis-gateway serve --deny-local-servers` disables automatic loopback enrollment. Other private-network model servers require an operator-configured origin, such as `--private-origin http://192.168.1.50:8080`. Hosted operation does not enable the loopback convenience. Gateway model access does not extend agent tool network permissions, and saving a connection does not replace its model check. See [gateway architecture](../gateway/architecture.md) and [provider settings](../consumer/vault-provider-settings.md).

Run [the aggregate qualification gate](qualification.md) before making a release claim. A successful server startup is a useful development check, but it does not establish source coverage, hosted isolation, backup interoperability or the browser journey.

## Implementation sources

- [pyproject.toml](/reference/files/pyproject.toml/)
- [package.json](/reference/files/package.json/)
- [CMakeLists.txt](/reference/files/CMakeLists.txt/)
- [consumer/config.py](/reference/files/consumer/config.py/)
- [consumer/cli.py](/reference/files/consumer/cli.py/)
- [consumer/gateway.py](/reference/files/consumer/gateway.py/)
- [model_gateway/service.py](/reference/files/model_gateway/service.py/)
- [model_gateway/__main__.py](/reference/files/model_gateway/__main__.py/)
- [packaging/build-wheel.sh](/reference/files/packaging/build-wheel.sh/)

[Developer manual](../index.md)
