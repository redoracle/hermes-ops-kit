"""Hermes Ops Kit — Tool JSON Schemas.

Expose only safe, bounded capabilities to the model.
All schemas follow the Hermes tool schema convention.
"""

# ── ai_provider_invoke ──────────────────────────────────────────────

AI_PROVIDER_INVOKE = {
    "name": "ai_provider_invoke",
    "description": (
        "Invoke an AI provider through Hermes Ops Kit. "
        "Routes to the best available provider/model. "
        "Use for chat, extraction, review, or read-only analysis. "
        "Never use for secrets, credential handling, or destructive actions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "enum": ["openai", "anthropic", "gemini", "deepseek", "github"],
                "description": "Provider to invoke.",
            },
            "operation": {
                "type": "string",
                "description": "Operation: chat, extract, review, analyze, or readonly.",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt or task. Must not contain secrets.",
            },
            "model": {
                "type": "string",
                "description": "Optional model override (e.g. gpt-5.4-mini).",
            },
        },
        "required": ["provider", "operation", "prompt"],
    },
}

AI_BRIDGE_INVOKE = {
    **AI_PROVIDER_INVOKE,
    "name": "ai_bridge_invoke",
    "description": (
        "Deprecated alias for ai_provider_invoke. Invoke an AI provider through "
        "Hermes Ops Kit for chat, extraction, review, or read-only analysis."
    ),
}

# ── ai_assistant_delegate ─────────────────────────────────────────

AI_ASSISTANT_DELEGATE = {
    "name": "ai_assistant_delegate",
    "description": (
        "Delegate a bounded read-only task to a configured remote Hermes assistant. "
        "Use for independent review, planning, code review, or security review. "
        "Do not use for secrets, credential handling, destructive shell actions, "
        "or production mutations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "assistant_id": {
                "type": "string",
                "description": "Configured assistant id, for example 'assistant-id'.",
            },
            "capability": {
                "type": "string",
                "description": "Capability to use: review, code_review, security_review, planning.",
            },
            "task": {
                "type": "string",
                "description": "Bounded task to delegate. Must not contain secrets.",
            },
            "context": {
                "type": "object",
                "description": "Optional non-secret structured context.",
            },
            "constraints": {
                "type": "object",
                "description": "Safety constraints: no_file_write, no_shell_execution, no_secret_access.",
            },
        },
        "required": ["assistant_id", "capability", "task"],
    },
}

# ── ai_usage_metrics ──────────────────────────────────────────────

AI_USAGE_METRICS = {
    "name": "ai_usage_metrics",
    "description": (
        "Return Hermes Ops Kit usage, provider health, assistant health, "
        "rate limits, and warnings. Use when checking routing readiness "
        "or operational risk."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["compact", "full", "models", "limits", "costs", "json"],
                "default": "compact",
            },
            "provider": {
                "type": "string",
                "description": "Optional single provider or assistant id.",
            },
        },
    },
}

# ── ai_key_rotate ─────────────────────────────────────────────────

AI_KEY_ROTATE = {
    "name": "ai_key_rotate",
    "description": (
        "Run safe key rotation workflows. Dry-run and status are safe. "
        "Applying rotations may require approval and must never expose raw secrets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "enum": ["openai", "anthropic", "google", "github", "deepseek", "all"],
            },
            "mode": {
                "type": "string",
                "enum": ["dry_run", "status", "render_env", "doctor", "apply"],
            },
        },
        "required": ["provider", "mode"],
    },
}

# ── ai_image_generate ──────────────────────────────────────────────

AI_IMAGE_GENERATE = {
    "name": "ai_image_generate",
    "description": (
        "Generate images using a configured AI image generation provider "
        "through Hermes Ops Kit image router. Routes to local ComfyUI "
        "(private), Gemini 2.5 Flash Image (fast/cheap), OpenAI DALL-E/gpt-image "
        "(high quality), or FAL.ai (cloud fallback) based on image_routes.yaml."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "enum": ["local-comfyui", "gemini", "openai", "fal"],
                "description": "Optional image generation provider. Uses default route if omitted.",
            },
            "route": {
                "type": "string",
                "enum": ["local", "fast", "quality", "fallback"],
                "description": "Optional configured image route name.",
            },
            "prompt": {
                "type": "string",
                "description": "Detailed description of the image to generate.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["landscape", "square", "portrait"],
                "default": "landscape",
                "description": "Aspect ratio for the generated image.",
            },
            "num_images": {
                "type": "integer",
                "default": 1,
                "minimum": 1,
                "maximum": 4,
                "description": "Number of images to generate.",
            },
            "model": {
                "type": "string",
                "description": "Optional model override for the selected provider.",
            },
            "image_path": {
                "type": "string",
                "description": "Optional reference image path for image-to-image generation.",
            },
            "edit_mode": {
                "type": "string",
                "enum": ["generate", "edit_background"],
                "default": "generate",
                "description": (
                    "Use edit_background with image_path to preserve the original "
                    "subject pixels and replace only the background."
                ),
            },
            "preserve_subject": {
                "type": "boolean",
                "default": False,
                "description": "When true with image_path, preserve the source subject while editing the background.",
            },
        },
        "required": ["prompt"],
    },
}

# ── ai_secret_backend_status ──────────────────────────────────────

AI_SECRET_BACKEND_STATUS = {
    "name": "ai_secret_backend_status",
    "description": (
        "Return the health and status of the Vaultwarden secret backend. "
        "Use for checking backend connectivity, secret backend state, and ref counts. "
        "Never returns raw secrets."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["health", "doctor", "list_refs"],
                "default": "health",
            }
        },
    },
}
