# CLAUDE.md

## Repository Summary

This repository is a Kubernetes-based LLM service operations diagnosis platform.

The MVP pipeline is:

k6 load test -> mock LLM service -> Prometheus metrics -> analyzer rule engine -> Markdown/JSON report.

## Start Here (resuming work / new machine)

Before doing anything else, read **HANDOFF.md** in the repo root. It records the
current progress (MVP Phase 0~9 done + finishing fixes verified end-to-end), the
remaining work, one known issue to fix first, and how to bring up the stack on a
fresh machine. CLAUDE.md = rules; HANDOFF.md = current state.

## Claude Role

You are the main implementer.

You may:
- create and edit source files
- implement phase-by-phase features
- write tests
- write Kubernetes YAML
- write docs
- update Makefile/scripts

You must:
- work phase by phase
- keep changes small
- run verification commands after each phase when possible
- explain what changed and how to verify it
- run Codex advisory review after meaningful changes when possible

## Project Rules

- Do not create all phases at once unless explicitly asked.
- Prefer working MVP over over-engineering.
- Keep mock LLM simple but realistic enough for backpressure metrics.
- Use prometheus_client for metrics.
- Use pydantic for analyzer schemas.
- Keep PromQL strings in analyzer/config/metrics.yaml.
- Rule code must use logical metric names, not raw PromQL.
- Reports must be generated as both Markdown and JSON from the same model.

## Forbidden Actions

- Do not commit secrets.
- Do not use git push --force.
- Do not use --no-verify unless the user explicitly asks.
- Do not delete large parts of the repo without asking.
- Do not modify generated reports unless asked.
