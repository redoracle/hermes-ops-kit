"""Hermes Ops Kit — Local ComfyUI Image Generation Adapter.

Uses a local ComfyUI instance via REST API.
ComfyUI must be running at the configured endpoint (default http://127.0.0.1:8188).

No API key required — local-only generation.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from image_routes.adapters.base import (
    BaseImageAdapter,
    ensure_cache_dir,
    make_output_path,
    build_envelope,
)


def _aspect_dims(aspect_ratio: str) -> tuple[int, int]:
    """Return (width, height) for common aspect ratios."""
    if aspect_ratio == "square":
        return 1024, 1024
    elif aspect_ratio == "portrait":
        return 768, 1344
    return 1344, 768  # landscape default


class LocalComfyUIAdapter(BaseImageAdapter):
    provider_name = "local-comfyui"
    default_model = "flux-local"

    # Default local ComfyUI endpoint — override via image_routes.yaml config.
    # 127.0.0.1:8188 is ComfyUI's standard local port. Change if running on a remote host.
    def __init__(self, endpoint: str = "http://127.0.0.1:8188", workflow: str = ""):
        self.endpoint = endpoint.rstrip("/")
        self.workflow_path = os.path.expanduser(
            workflow or "~/.hermes/ops-kit/workflows/flux-text2image.json"
        )

    def is_available(self) -> bool:
        """Check that ComfyUI is running and reachable."""
        try:
            url = f"{self.endpoint}/system_stats"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return "system" in data
        except Exception:
            return False

    def get_system_info(self) -> dict:
        """Return ComfyUI system information if available."""
        try:
            url = f"{self.endpoint}/system_stats"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            return {"error": "ComfyUI not reachable"}

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        aspect_ratio: str = "landscape",
        num_images: int = 1,
        image_path: str | None = None,
        **kwargs,
    ) -> dict:
        start = self._start_timer()

        # Check availability first
        if not self.is_available():
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model or self.default_model,
                error=f"ComfyUI not reachable at {self.endpoint}. Start it with: comfyui",
                error_type="BackendUnavailable",
                duration_ms=self._elapsed_ms(start),
            )

        ensure_cache_dir()
        image_paths = []
        width, height = _aspect_dims(aspect_ratio)

        # Load external workflow if configured, else use inline default
        external_workflow = None
        if os.path.isfile(self.workflow_path):
            try:
                with open(self.workflow_path) as wf:
                    external_workflow = json.load(wf)
            except Exception:
                external_workflow = None

        try:
            for i in range(min(num_images, 4)):
                prompt_url = f"{self.endpoint}/prompt"

                if external_workflow:
                    # Clone workflow and inject prompt + dimensions
                    workflow = json.loads(json.dumps(external_workflow))
                    for _nid, nd in workflow.items():
                        if isinstance(nd, dict):
                            ins = nd.get("inputs", {})
                            if isinstance(ins, dict):
                                if "text" in ins:
                                    ins["text"] = prompt
                                if "width" in ins:
                                    ins["width"] = width
                                if "height" in ins:
                                    ins["height"] = height
                    payload_data = {
                        "prompt": workflow,
                        "client_id": f"hermes-ops-kit-{os.getpid()}",
                    }
                else:
                    payload_data = {
                        "prompt": {
                            "3": {
                                "class_type": "KSampler",
                                "inputs": {
                                    "seed": int.from_bytes(os.urandom(4), "big"),
                                    "steps": 20,
                                    "cfg": 7.0,
                                    "sampler_name": "euler",
                                    "scheduler": "normal",
                                    "denoise": 1.0,
                                    "model": ["4", 0],
                                    "positive": ["6", 0],
                                    "negative": ["7", 0],
                                    "latent_image": ["5", 0],
                                },
                            },
                            "4": {
                                "class_type": "CheckpointLoaderSimple",
                                "inputs": {"ckpt_name": "flux1-dev.safetensors"},
                            },
                            "5": {
                                "class_type": "EmptyLatentImage",
                                "inputs": {
                                    "width": width,
                                    "height": height,
                                    "batch_size": 1,
                                },
                            },
                            "6": {
                                "class_type": "CLIPTextEncode",
                                "inputs": {
                                    "text": prompt,
                                    "clip": ["4", 1],
                                },
                            },
                            "7": {
                                "class_type": "CLIPTextEncode",
                                "inputs": {
                                    "text": "bad quality, blurry, distorted",
                                    "clip": ["4", 1],
                                },
                            },
                            "8": {
                                "class_type": "VAEDecode",
                                "inputs": {
                                    "samples": ["3", 0],
                                    "vae": ["4", 2],
                                },
                            },
                            "9": {
                                "class_type": "SaveImage",
                                "inputs": {
                                    "filename_prefix": f"hermes_ops_kit_{i}",
                                    "images": ["8", 0],
                                },
                            },
                        },
                        "client_id": f"hermes-ops-kit-{os.getpid()}",
                    }

                payload = json.dumps(payload_data).encode()
                req = urllib.request.Request(
                    prompt_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(
                    req, timeout=self._generation_timeout()
                ) as resp:
                    result = json.loads(resp.read())

                # Poll for completion
                prompt_id = result.get("prompt_id", "")
                if prompt_id:
                    self._poll_until_done(prompt_id)
                    history_url = f"{self.endpoint}/history/{prompt_id}"
                    req2 = urllib.request.Request(history_url)
                    with urllib.request.urlopen(req2, timeout=10) as resp2:
                        history = json.loads(resp2.read())
                    img_data = self._extract_image_from_history(history, prompt_id)
                    if img_data:
                        path = make_output_path(self.provider_name, "png")
                        with open(path, "wb") as f:
                            f.write(img_data)
                        os.chmod(path, 0o644)
                        image_paths.append(path)

        except Exception as e:
            from security.redaction import redact

            return build_envelope(
                False,
                provider=self.provider_name,
                model=model or self.default_model,
                error=redact(str(e)),
                error_type=type(e).__name__,
                duration_ms=self._elapsed_ms(start),
            )

        if not image_paths:
            return build_envelope(
                False,
                provider=self.provider_name,
                model=model or self.default_model,
                error="ComfyUI completed but no images were saved. Check the workflow output node.",
                error_type="NoImageData",
                duration_ms=self._elapsed_ms(start),
            )

        return build_envelope(
            True,
            provider=self.provider_name,
            model=model or self.default_model,
            image_paths=image_paths,
            caption=f"Generated locally with {self.default_model}",
            duration_ms=self._elapsed_ms(start),
            extra={
                "endpoint": self.endpoint,
                "aspect_ratio": aspect_ratio,
                "num_requested": num_images,
                "num_generated": len(image_paths),
            },
        )

    def _poll_until_done(self, prompt_id: str) -> None:
        """Poll ComfyUI until the prompt completes or times out."""
        import time

        deadline = time.time() + self._generation_timeout()
        last_error = ""
        while time.time() < deadline:
            try:
                url = f"{self.endpoint}/history/{prompt_id}"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    history = json.loads(resp.read())
                entry = history.get(prompt_id, {})
                if entry:
                    # Check for explicit completion: status field or outputs present
                    status = entry.get("status", {})
                    if (
                        isinstance(status, dict)
                        and status.get("completed", True) is False
                    ):
                        time.sleep(0.5)
                        continue
                    outputs = entry.get("outputs", {})
                    if outputs:
                        return  # Images are ready
                    # No outputs yet but entry exists — keep polling
            except urllib.error.URLError as e:
                last_error = str(e)
                # Transient network error — keep polling
            except Exception as e:
                last_error = str(e)
            time.sleep(0.5)
        raise TimeoutError(
            f"ComfyUI prompt {prompt_id} did not complete within "
            f"{self._generation_timeout()}s. Last error: {last_error}"
        )

    def _extract_image_from_history(
        self, history: dict, prompt_id: str
    ) -> bytes | None:
        """Extract the generated image from ComfyUI history response."""
        try:
            outputs = history.get(prompt_id, {}).get("outputs", {})
            for _node_id, node_output in outputs.items():
                images = node_output.get("images", [])
                if images:
                    filename = images[0].get("filename", "")
                    subfolder = images[0].get("subfolder", "")
                    img_type = images[0].get("type", "output")
                    params = urllib.parse.urlencode(
                        {
                            "filename": filename,
                            "subfolder": subfolder,
                            "type": img_type,
                        }
                    )
                    img_url = f"{self.endpoint}/view?{params}"
                    req = urllib.request.Request(img_url)
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        return resp.read()
        except Exception:
            pass
        return None

    @staticmethod
    def _generation_timeout() -> int:
        return 180
