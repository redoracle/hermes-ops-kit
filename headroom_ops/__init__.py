"""Hermes Ops Kit — Headroom proxy integration (package headroom_ops:
named to avoid shadowing the installed headroom-ai 'headroom' module).

Route-overlay management for the Headroom compression proxy:
  settings.py   — bundled/deployed headroom.yaml loading
  daemon.py     — proxy lifecycle (up/down/status, pidfile, health)
  reconcile.py  — desired-state → config.yaml reconciliation
  manager.py    — CLI (status, doctor, up, down, enable, disable,
                  reconcile, stats, export)
"""
