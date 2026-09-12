---
title: "Install Sturgis on your Linux machine"
description: "Install a matching platform wheel, keep data private, and start the local interface without a Node runtime."
sidebar:
  order: 2
---

Self-hosted mode is intended for an installation whose OS user you trust. Each agent gets a separate worker process and fixed data scope. The process still has the ordinary filesystem authority of that OS identity.

## Prerequisites

Use a release wheel matching the machine's Linux architecture, CPython version, and system-library baseline. The exercised baseline is Ubuntu 24.04 and CPython 3.12. Linux must provide SQLite's runtime library and the C++ runtime; relevant Ubuntu packages include `python3.12-venv`, `libsqlite3-0`, and `libstdc++6`.

A wheel contains the Python services and CLI, native memory executable, compiled interface, fonts, and attribution. It does not need the source checkout, Node, or a compiler at runtime. Obtain the wheel from your operator's release process and verify the supplied SHA-256 sidecar where one is provided.

## Worked example: install and start locally

With one matching `sturgis_core-*.whl` in the current directory:

```sh
python3.12 -m venv "$HOME/.local/opt/sturgis"
"$HOME/.local/opt/sturgis/bin/python" -m pip install ./sturgis_core-*.whl
"$HOME/.local/opt/sturgis/bin/sturgis" setup
"$HOME/.local/opt/sturgis/bin/sturgis" serve
```

Open `http://127.0.0.1:8787`. First-owner setup uses the private `bootstrap.token` written under the data root. Follow [account setup](account-and-agents.md); bootstrap also signs you in.

The default data root is `~/.local/share/sturgis`. Client configuration is under `~/.config/sturgis`, respecting `XDG_CONFIG_HOME`. To select another private data root, run `sturgis setup --root /absolute/private/data` before serving. Keep data outside the installed program or virtual environment. Add the environment's `bin` directory to PATH if you want the shorter commands in this manual.

## Building a wheel yourself

From a checkout, install CPython 3.12, a C++20 compiler, CMake, SQLite development headers, and Node/npm compatible with the lockfile. Then run:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]' setuptools wheel
npm ci
bash packaging/build-wheel.sh .build/release
```

Install the resulting wheel into the target environment before serving it. The script builds the web client and a separate release native binary; it does not reuse an instrumented qualification executable.

## Expected result and recovery

`setup` reports native availability and the selected paths. If it reports that the binary is absent, install a complete wheel or finish the source build; do not assume memory is working from the web page alone. `sturgis doctor --agent AGENT_ID` can inspect local availability and an authenticated runtime.

Stop the existing service before upgrading its installed code or starting another process on the same root. One root has one placement owner. Back up agents through [the supported archive flow](../settings/backup-restore.md), rather than copying live SQLite/WAL files. For public access, continue with [hosted operation](hosted.md).
