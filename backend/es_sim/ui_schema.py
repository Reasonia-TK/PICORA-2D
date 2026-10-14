"""PICORA-2D の UI 同梱 JSON Schema を生成する。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .project2d import project_schema

SNAPSHOT = Path(__file__).resolve().parents[2] / "ui" / "src" / "schema" / "project.schema.json"


def snapshot_text() -> str:
    return json.dumps(project_schema(), ensure_ascii=False, indent=1) + "\n"


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0]) if args else SNAPSHOT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(snapshot_text(), encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
