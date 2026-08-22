#!/usr/bin/env python3
"""
Gun Art Online mining cron task.

Designed for GitHub Actions:
- Run once and exit.
- Check mining status with the fewest practical requests.
- Collect only when the current mining session is ready.
- Eat food only when HP/MP is below the configured threshold.
- Start a new mining session if possible.
"""

from outputs.gunart_mining_cron import run_once


if __name__ == "__main__":
    raise SystemExit(run_once())
