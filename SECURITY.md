# Security policy

> **ACTIVE DEVELOPMENT — NOT PRODUCTION-READY.** Sturgis is experimental agent software. Do not treat a development build, passing test suite, or example deployment as a production security certification.

## Supported versions

There is no stable production release or supported-version matrix declared here. Security fixes are evaluated against current development source. No response deadline, maintenance period, or support SLA is promised by this document.

## Report a vulnerability privately

Open the [Sturgis Security page](https://github.com/Sidiora-Labs/sturgis-agent-harness/security) and choose **Report a vulnerability** if private vulnerability reporting is enabled. The option depends on the repository's settings and may not be available.

Otherwise, contact the repository maintainers through a private channel they have published or already established with you. This checkout has no configured public reporting address; do not guess an email address or send a report to an unrelated project. If you cannot find a private channel, ask the maintainer for one without posting exploit details or affected user data publicly.

Do not open a public issue or pull request containing an unpatched exploit, credentials, real conversation transcripts, private database contents, or an accessible attack target. Coordinate disclosure through the private reporting channel.

## What to include

- The commit or build identifier and relevant configuration, with secrets removed.
- Affected component and execution mode: core, consumer, standalone gateway, embedded gateway, self-hosted, or hosted.
- Minimal reproduction steps using disposable data, plus expected and observed behavior.
- The authority or confidentiality boundary crossed and the practical impact you observed.
- Relevant sanitized logs or a small proof of concept. Identify untested assumptions separately.

Test only systems and accounts you own or are authorized to assess. Avoid destructive actions, unnecessary access to other users' data, and attempts against provider infrastructure. If a credential was exposed, revoke or rotate it through its actual owner; removing it from a report does not revoke it.

## Boundaries to examine

Useful reports include cross-account or cross-agent access; authentication or CSRF bypass; credential or token leakage; gateway client-key scope bypass; unintended private-network access; unsafe archive paths; plugin or tool isolation escape; authority reuse after cancellation; and incorrect disclosure through logs, errors, artifacts, or conversation replay.

The gateway is an independent subsystem. An agent's restricted gateway key should not expose upstream credentials or confer gateway administration. Agent tool grants, filesystem access, provider access, and consumer account authorization are separate controls.

Hosted worker isolation depends on the documented Linux facilities and operator configuration. It does not establish a hostile-kernel boundary or host-network isolation. See [packaging and hosted-worker limits](packaging/README.md) and the [developer authority model](documentation/developers/src/content/docs/guides/architecture/authority-evidence.md).

## Handling sensitive material

Keep installation tokens, API keys, OAuth tokens, session material, private keys, and runtime data outside Git. Sanitizing an error message alone may not sanitize attached screenshots, backups, source artifacts, or recorded provider traffic. Use synthetic examples in public tests and documentation.

If sensitive material reaches Git history, tell the maintainers privately and revoke exposed credentials. Coordinate any history rewrite rather than force-pushing over other contributors' work.

The project's [Apache License 2.0](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.md) govern the respective code. They do not replace security review, operating controls, or responsible handling of user data.
