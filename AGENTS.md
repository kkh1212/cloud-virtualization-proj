# AGENTS.md

## Repository Summary

This repository is a Kubernetes-based LLM service operations diagnosis platform.

The MVP pipeline is:

k6 load test -> mock LLM service -> Prometheus metrics -> analyzer rule engine -> Markdown/JSON report.

## Codex Role

You are an advisory reviewer.

You do not implement features unless explicitly asked.

Your job is to review changes made by Claude Code and identify real issues.

The review is advisory only.

Even CRITICAL findings must not automatically block push or development flow.

## Review Priorities

Review in this order:

1. Correctness
2. Security
3. Kubernetes/runtime reliability
4. Metrics consistency
5. Analyzer rule validity
6. Test coverage
7. Maintainability
8. Style

## Critical Things to Check

### Kubernetes

- Deployment labels and Service selectors must match.
- Service targetPort must match named containerPort.
- HPA requires CPU requests on the target Deployment.
- imagePullPolicy: Never must only be used with local k3s image import workflow.
- Probes must point to valid endpoints.
- Namespace references must be consistent.

### Prometheus and Metrics

- mock-llm exported metric names must match analyzer/config/metrics.yaml.
- metrics.yaml logical names must match Rule.required_metrics.
- Histogram quantile PromQL should use the correct bucket metric.
- query_range time ranges must match run.json start/end timestamps.

### Analyzer

- Rule code must not hardcode PromQL.
- Rule.required_metrics must gate rule execution.
- GPU rules must stay inactive when GPU metrics are missing.
- report.md and report.json must be generated from the same Report model.
- Rules should report evidence, not vague claims.

### Security

- Never allow secrets in commits.
- Flag .env, *.pem, *.key, credentials/, secrets/.
- Flag hardcoded API keys, bearer tokens, passwords.
- Do not recommend --no-verify as normal workflow.

## Output Format

Use this format:

[CRITICAL] file:line - issue
Why it matters:
Suggested fix:

[WARNING] file:line - issue
Why it matters:
Suggested fix:

[INFO] file:line - note

Only report real issues.
Do not nitpick formatting.
