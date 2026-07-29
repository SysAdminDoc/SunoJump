#!/usr/bin/env python3
"""Minimal entry point for the isolated native audio decoder."""
from safe_audio import worker_cli_main


if __name__ == "__main__":
    raise SystemExit(worker_cli_main())
