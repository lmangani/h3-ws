#!/usr/bin/env python3
"""Download MiniMax-H3 FL2VA + Ref2VA checkpoints into ./models/MiniMax-H3."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from h3_paths import default_model_dir  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Download MiniMaxAI/MiniMax-H3 weights")
    p.add_argument("--repo", default="MiniMaxAI/MiniMax-H3")
    p.add_argument("--local-dir", type=Path, default=default_model_dir())
    p.add_argument("--fl2va-only", action="store_true", help="Skip Ref2VA (t2va / first-last only)")
    args = p.parse_args()

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise SystemExit("huggingface_hub is required: pip install huggingface_hub") from None

    dest = args.local_dir.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    allow = ["model_index.json", "FL2VA/*"]
    if not args.fl2va_only:
        allow.append("Ref2VA/*")
    print(f"Downloading {args.repo} → {dest}")
    snapshot_download(
        repo_id=args.repo,
        local_dir=str(dest),
        allow_patterns=allow,
    )
    fl = dest / "FL2VA"
    if not fl.is_dir():
        raise SystemExit(f"Download finished but {fl} is missing")
    print(f"OK: {dest}")
    if (dest / "Ref2VA").is_dir():
        print(f"     Ref2VA present")
    elif args.fl2va_only:
        print("     Ref2VA skipped (--fl2va-only)")
    else:
        print("     warning: Ref2VA tree missing")


if __name__ == "__main__":
    main()
