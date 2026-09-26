#!/usr/bin/env python3
"""Run the LocalNote server against the local test vault for Reader testing.

This launcher injects an explicit ``Settings`` object into ``create_app`` so the
per-instance saved configuration (``~/.config/localnote/instances/.../settings.json``)
cannot override the vault root. Passing explicit settings makes the config
repository isolated, so nothing is read from or written to the user's real
settings file.

Usage:
    .venv/bin/python scripts/run_reader_local.py [--port 3780] [--vault test-vault]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from server.api.main import create_app  # noqa: E402
from server.config import Settings  # noqa: E402


def build_settings(vault_root: Path, port: int) -> Settings:
    """Build settings for the local test run without touching saved config."""
    settings = Settings()
    settings.vault.root = vault_root
    settings.server.host = "127.0.0.1"
    settings.server.port = port
    settings.server.cors_origins = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    return settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=3780)
    parser.add_argument(
        "--vault",
        default="test-vault",
        help="Vault root (relative paths resolve against the project root)",
    )
    args = parser.parse_args()

    vault_root = Path(args.vault)
    if not vault_root.is_absolute():
        vault_root = PROJECT_ROOT / vault_root
    vault_root = vault_root.resolve()

    if not vault_root.is_dir():
        print(f"vault root is not a directory: {vault_root}", file=sys.stderr)
        return 2

    settings = build_settings(vault_root, args.port)
    app = create_app(settings)

    print(f"vault root : {vault_root}")
    print(f"reader db  : {vault_root / '.localnote' / 'reader.db'}")
    print(f"listening  : http://127.0.0.1:{args.port}")
    sys.stdout.flush()

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
