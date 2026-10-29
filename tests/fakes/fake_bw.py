#!/usr/bin/env python3
"""A read-only stand-in for the Bitwarden CLI, for contract tests of ops-kit's own calls.

It answers only what the documented read paths ask (status, sync, lock, list/get items,
config server) from a JSON fixture named by FAKE_BW_ITEMS. Anything else, such as creating,
editing or deleting an item, exits 64 so a "dry run" that writes anything fails loudly.
It proves ops-kit issues the calls it should; it says nothing about a live vault.
"""

from __future__ import annotations

import json
import os
import sys

args = sys.argv[1:]
items = json.load(open(os.environ["FAKE_BW_ITEMS"]))
unlocked = bool(os.environ.get("BW_SESSION"))

if args[:1] == ["status"]:
    print(
        json.dumps(
            {
                "serverUrl": "https://vault.test",
                "status": "unlocked" if unlocked else "locked",
                "userEmail": "ops@example.test",
                "lastSync": "2026-01-01T00:00:00.000Z",
            }
        )
    )
elif args[:2] == ["config", "server"]:
    print("Saved setting `serverUrl`.")
elif args[:1] == ["sync"]:
    print("Syncing complete.")
elif args[:1] == ["lock"]:
    print("Your vault is locked.")
elif args[:2] == ["list", "items"]:
    search = args[args.index("--search") + 1] if "--search" in args else None
    print(json.dumps([i for i in items if not search or search in i["name"]]))
elif args[:2] == ["list", "folders"]:
    print("[]")
elif args[:2] == ["get", "item"]:
    found = [i for i in items if i["id"] == args[2]]
    if not found:
        print("Not found.", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(found[0]))
else:
    print(f"fake bw: unsupported call: {' '.join(args[:3])}", file=sys.stderr)
    sys.exit(64)
