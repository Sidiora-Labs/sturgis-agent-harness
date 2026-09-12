---
title: "Build and operate the packaged application"
description: "Ship Python, the native executable and compiled client as one platform-specific installation."
---

The release wheel contains the Python core and consumer, compiled web assets/fonts, a native ELF memory executable and third-party notices. Because it bundles native code, it is a platform wheel rather than a universal Python package.

```sh
bash packaging/build-wheel.sh .build/release
python3.12 -m venv /tmp/sturgis-installed
/tmp/sturgis-installed/bin/python -m pip install .build/release/sturgis_core-*.whl
/tmp/sturgis-installed/bin/sturgis setup --root /absolute/private/sturgis-data
/tmp/sturgis-installed/bin/sturgis serve
```

Choose a wheel compatible with the host architecture, Python version and system library baseline. This example creates a separate installation; it does not transfer an existing runtime's secrets or authority.

The builder compiles release-mode native code in a temporary staging tree and packages the existing production modules. It writes SHA-256 sidecars. `STURGIS_USE_BUILT_WEB=1` may reuse exact already-qualified assets; it is not permission to package stale frontend output. Node is a build dependency, not an installed service.

## Operator configuration

Settings validates immutable listener, public-origin, mode, source/native paths, private-network origin exceptions, browser path and worker limits. The default listener is localhost. A wildcard listener requires an explicit public origin so Host/Origin and CSRF checks have one authority.

Run data under a private path outside installed code. Packaged installations select their bundled native executable. Keep bootstrap tokens, CLI sessions, vault keys and worker IPC keys out of images and ordinary content exports.

The provided systemd unit and container file are deployment examples, not proof of a remote deployment. Hosted mode still requires the outer environment to permit its Linux namespaces; a container setting cannot manufacture unsupported kernel privileges.

## Verify the actual package

Installation qualification builds and installs a real wheel outside the checkout, verifies packaged web bytes, starts real native workers, and exercises HTTP backup/restore. A development import from the repository is not equivalent evidence.

For failures, inspect [debugging](debugging.md). For changing persistent state or reusing old evidence, read [release/migration](release-migration.md). Documentation sites are separate static outputs and are covered by their own [documentation workflow](../getting-started/documentation.md).

## Implementation sources

- [packaging/build-wheel.sh](/reference/files/packaging/build-wheel.sh/)
- [packaging/Dockerfile](/reference/files/packaging/Dockerfile/)
- [packaging/sturgis.service](/reference/files/packaging/sturgis.service/)
- [pyproject.toml](/reference/files/pyproject.toml/)
- [consumer/config.py](/reference/files/consumer/config.py/)
- [consumer/cli.py](/reference/files/consumer/cli.py/)
- [consumer/__init__.py](/reference/files/consumer/__init__.py/)
- [consumer/__main__.py](/reference/files/consumer/__main__.py/)

[Developer manual](../index.md)

