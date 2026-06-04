# Changelog

All notable changes to Hermes Ops Kit.

## [0.2.0] — 2026-06-04

### Initial Public Release

**Provider Adapters** — 5 LLM providers (OpenAI, Anthropic, Google Gemini,
GitHub, DeepSeek) with shared redaction pipeline.

**Key Rotation** — Two-phase rotation (store → smoke → activate → revoke)
backed by self-hosted Vaultwarden/Bitwarden. Provider rotators for OpenAI
(full-auto), Google Gemini (full-auto), Anthropic (partial), DeepSeek
(semi-manual), and GitHub (token minting).

**Usage Metrics** — Concurrent provider health checks, rate limits, cost
telemetry, CLI version probes, with rich terminal and JSON output.

**Route Manager** — CLI for managing Hermes routing configuration (primary,
utility, auxiliary, fallback chains) with profile presets (cheap, balanced,
max-quality).

**Image Routes** — Separate image generation routing layer (ComfyUI local,
Gemini Image, OpenAI DALL-E/gpt-image, FAL.ai) with priority-based fallback.

**Assistant Manager** — Config-driven remote Hermes agent delegation with
capability-based policy, result sanitization, and JSONL audit trail.

**MCP Auditor** — Security audit for MCP servers and tools with risk
classification and atomic whitelisting.

**Security** — Vaultwarden secret backend (3 auth modes), 16-pattern redaction,
secret scanner gate, atomic env writes (chmod 600 + fsync), safe Bitwarden CLI
wrapper, SHA-256 fingerprinting.

**Environment Projection** — Bootstrap `.env` → Vaultwarden → `.env.generated`
pipeline with 23 env var → secret ref mappings.

**Tests** — 92 tests across security, snapshots, CLI integration, and
simulator scenarios.

[0.2.0]: https://github.com/your-org/hermes-ops-kit/releases/tag/v0.2.0
