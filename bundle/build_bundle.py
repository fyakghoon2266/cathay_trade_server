"""Build the Codex Skill Market bundle.

Identity is by display_name; token is a shared entry-ticket. So one bundle is
enough for everyone — no per-person personalization required.

Usage:
    python build_bundle.py \\
        --server https://market.example.com \\
        --token arena-2026 \\
        --out /tmp/codex-bundles \\
        [--name codex-skill-market]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent


def render(out_dir: Path, token: str, server_url: str) -> Path:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    for src in HERE.iterdir():
        if src.name in {"build_bundle.py", "__pycache__"}:
            continue
        dst = out_dir / src.name
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    arena = out_dir / ".codex-arena"
    arena.mkdir(exist_ok=True)
    (arena / "token.txt").write_text(token + "\n", encoding="utf-8")

    for fname in ("AGENTS.md", "README.md"):
        p = out_dir / fname
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("__SERVER_URL__", server_url), encoding="utf-8")

    return out_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, help="Public server base URL")
    ap.add_argument("--token", default="arena-2026", help="Shared entry-ticket token")
    ap.add_argument("--out", default="/tmp/codex-bundles", help="Output directory")
    ap.add_argument("--name", default="codex-skill-market", help="Top-level folder name inside the archive")
    ap.add_argument("--format", choices=["zip", "tar.gz"], default="tar.gz")
    args = ap.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    staging = out_root / f"_staging_{args.name}"
    target_dir = staging / args.name
    render(target_dir, args.token, args.server.rstrip("/"))

    archive_format = "gztar" if args.format == "tar.gz" else "zip"
    archive_ext = ".tar.gz" if args.format == "tar.gz" else ".zip"
    archive_path = out_root / f"{args.name}{archive_ext}"
    if archive_path.exists():
        archive_path.unlink()
    shutil.make_archive(str(out_root / args.name), archive_format, root_dir=staging)
    shutil.rmtree(staging)
    print(f"built {archive_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
