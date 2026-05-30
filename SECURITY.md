# Security Policy

## Supported Versions

This repository is currently maintained on the `main` branch.
Security fixes will be applied to the latest code on `main`.

| Version | Supported |
| --- | --- |
| main | Yes |
| older commits / forks | No |

## Reporting a Vulnerability

Please do **not** open a public GitHub issue for suspected security vulnerabilities.

Instead, report the issue privately to the maintainer with:
- a short description of the problem;
- affected file(s), module(s), or command(s);
- reproduction steps or a proof of concept, if available;
- impact assessment, including whether credentials, tokens, or data may be exposed;
- any suggested mitigation.

If you enable GitHub private vulnerability reporting for this repository, prefer that channel.
Otherwise, use the maintainer's private contact channel and include the repository name in the subject.

## Response Expectations

The maintainer will aim to:
- acknowledge receipt within 7 days;
- assess severity and reproducibility;
- prepare a fix or mitigation before public disclosure when possible.

Please avoid sharing exploit details publicly until a fix or mitigation is available.

## Scope

This project consumes the public Open Bus Stride API and does not handle personal data (PII).
Issues to report include, but are not limited to:
- dependency vulnerabilities affecting users of this library;
- code paths that could lead to data corruption in generated dashboards;
- HTML/PDF rendering issues that could enable XSS in the static dashboard.
