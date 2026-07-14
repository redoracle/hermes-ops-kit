---
name: credential-leak-audit
description: Find and fix credential leaks in log/display output — the URL sanitization pattern from PRs #38823 and #38853.
---

# Credential Leak Audit

Pattern for finding and fixing credential leaks in Python code that logs or displays
configuration values to terminals, log files, or structured output.

## The pattern

When code reads a `base_url` from config and displays it without sanitization, the URL
may carry embedded credentials:

```
https://user:pass@api.example.com/v1        # userinfo in netloc
https://api.example.com/v1?api_key=sk-abc   # query-string API token
https://api.example.com/v1#fragment          # rarely sensitive, but strip anyway
```

These leak through:
- `logger.info("... %s ...", base_url)` — log files, aggregators
- `print(f"base_url: {base_url}")` — terminal output, CI logs
- `check_info(f"base_url: {base_url}")` — diagnostic commands

## Detection

Search for config values that are interpolated into output without a sanitizer:

```bash
# Find base_url references in log/print calls
rg -n 'base_url.*f"|f".*base_url|logger\.\w+\(.*base_url|print\(.*base_url' --glob '!tests/**'

# Find API key references (should NEVER be in output)
rg -n 'api_key.*f"|f".*api_key|logger\.\w+\(.*api_key|print\(.*api_key' --glob '!tests/**'
```

If API keys are found in output: **HIGH severity — fix immediately.**

## The fix

Add a sanitization function that strips sensitive URL components:

```python
def _sanitize_url_for_display(url: str) -> str:
    """Strip userinfo, query, and fragment from a URL for terminal display."""
    if not url:
        return url
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        # Python's ParseResult has no `userinfo` field — split netloc manually
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.split("@")[-1]
        sanitized = parsed._replace(netloc=netloc, query="", fragment="")
        return urlunparse(sanitized)
    except Exception:
        return "<url-redacted>"  # never return raw on failure
```

Apply it at every display site:

```python
# Before (LEAKS credentials)
check_info(f"base_url: {base_url}")
label += f" at {fb_url}"

# After (SAFE)
check_info(f"base_url: {_sanitize_url_for_display(base_url)}")
label += f" at {_sanitize_url_for_display(fb_url)}"
```

## When to place the sanitizer

| Location | Approach |
|----------|----------|
| Same module, single use | Inline the function |
| Same module, multiple uses | Module-level helper |
| Multiple modules | Extract to a shared utility (e.g., `utils.py`) |

For `hermes-agent`, `utils.py` already exists and is imported widely — it's the natural home
for a shared `_sanitize_url_for_logging`.

## Verification

Test with known-bad URLs:

```python
tests = [
    ("https://user:pass@host/v1?key=sk-secret#f", "https://host/v1"),
    ("https://api.openai.com/v1", "https://api.openai.com/v1"),
    ("", ""),
]
for url, expected in tests:
    assert sanitize(url) == expected
```

## Real examples

### PR #38853 — Route logging

`set_runtime_main()` logged `_RUNTIME_MAIN_BASE_URL` directly via `logger.info()`.
The base URL can come from `OPENAI_BASE_URL` env var, which users commonly set with
embedded credentials. Added `_sanitize_url_for_logging()` and applied it before
interpolation.

### PR #38823 — Doctor verbose output

`_show_verbose_route_details()` and `_show_verbose_fallback_chain()` displayed
`base_url` from config.yaml via `check_info()`. Added `_sanitize_url_for_display()`
and applied at both call sites.

## Related patterns

- [[review-response]] — systematic approach to receiving and acting on review feedback
- [[rebase-conflict]] — rebasing stale PR branches onto upstream main
