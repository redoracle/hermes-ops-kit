# Route Profile Design

Hermes Ops Kit provides route management CLI tooling for Hermes model routing
configuration. It reads from `~/.hermes/config.yaml` as the authoritative
source of truth and layers display metadata and profile presets on top.

## Two Separate Routing Layers

| Layer              | Config Source             | Purpose                                            |
|--------------------|---------------------------|----------------------------------------------------|
| ROUTE + AUX ROUTES | `~/.hermes/config.yaml`   | "Which LLM handles this text task?"                |
| IMAGE ROUTES       | `~/.hermes/ops-kit/image_routes.yaml` | "Which image backend renders this?"   |

**They are different systems.** AUX ROUTE `vision = gemini-2.5-flash` means
*image analysis* (looking at screenshots), NOT image generation. Image
generation is a tool/media backend concern.

## Config Files

### `~/.hermes/config.yaml` (Source of Truth)

The native Hermes config. Ops Kit reads these sections:

- `model.default` / `model.provider` — primary route
- `fallback_providers` — fallback chain
- `auxiliary.*` — vision, web_extract, compression, skills_hub, approval,
  mcp, title_generation, triage_specifier
- `image_gen.provider` / `image_gen.model` — native image generation config
- `plugins.enabled` — which plugins are active

### `~/.hermes/ops-kit/routes.yaml` (Display Metadata + Profiles)

Route display labels, cost classes, and profile presets. This file is used
by `hermes-route-manager` and `hermes-usage` for display purposes. It does NOT
drive routing decisions — Hermes config.yaml is the authoritative source.

```yaml
profiles:
  cheap:
    primary: { provider: deepseek, model: deepseek-v4-flash }
    fallback: [{ provider: gemini, model: gemini-2.5-flash }]
  balanced:
    primary: { provider: copilot, model: gpt-5.4-mini }
    fallback: [{ provider: gemini, model: gemini-2.5-flash }, { provider: anthropic, model: claude-haiku-4-5 }]
  max-quality:
    primary: { provider: openai, model: gpt-5.5 }
    fallback: [{ provider: anthropic, model: claude-opus-4-8 }]
```

### `~/.hermes/ops-kit/image_routes.yaml` (Image Routing)

```yaml
default_route: local
routes:
  local: { provider: local-comfyui, model: flux-local, priority: 10 }
  fast: { provider: gemini, model: gemini-2.5-flash-image, priority: 20 }
  quality: { provider: openai, model: gpt-image-2, priority: 30 }
  fallback: { provider: fal, model: fal-ai/flux-2-pro, priority: 40 }
policies:
  prefer_local: true
  allow_cloud_fallback: true
  max_generation_seconds: 180
  output_dir: "~/.hermes/cache/images"
```

## apply-profile Flow

```
hermes-route-manager apply-profile cheap
  │
  ├─ 1. Read ~/.hermes/ops-kit/routes.yaml → get "cheap" profile
  ├─ 2. Read ~/.hermes/config.yaml → current state
  ├─ 3. Write new model/fallback to config.yaml
  ├─ 4. Validate: config.yaml parses, model + provider valid
  └─ 5. `hermes-usage --compact` to confirm
```

Apply-profile **patches native Hermes config keys** (`model.default`,
`model.provider`, `fallback_providers`, `auxiliary.*`). It never requires
Hermes to read `routes.yaml`. The routes.yaml file is metadata only.

## Defining Custom Profiles

Add entries to `~/.hermes/ops-kit/routes.yaml`:

```yaml
profiles:
  my-profile:
    primary: { provider: openai, model: gpt-5.4-mini }
    fallback:
      - { provider: anthropic, model: claude-sonnet-4-6 }
      - { provider: gemini, model: gemini-2.5-flash }
    aux_overrides:
      vision: { provider: gemini, model: gemini-2.5-flash }
      web_extract: { provider: gemini, model: gemini-2.5-flash }
```

Then apply: `hermes-route-manager apply-profile my-profile`

## Fallback Chain Design

Fallback chains are ordered priority lists. When the primary provider fails
(rate limit, timeout, auth error, or model unavailable), Hermes tries each
fallback in order. Ops Kit helps design these chains:

```bash
hermes-route-manager fallback list
hermes-route-manager fallback add openai gpt-5.4-mini
hermes-route-manager fallback remove openai
```

Good fallback chains:
- **Diverse providers** — avoid both OpenAI entries in a row
- **Cost gradient** — cheaper models later in the chain
- **Same capability** — don't fall back from vision to non-vision models
- **Fast first** — put your fastest fallback first
