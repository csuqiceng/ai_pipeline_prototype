from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULTS = {
    "spd_pct": 50.0,
    "acc_pct": 60.0,
    "dec_pct": 60.0,
}
LEGACY_KEYS = {
    "spd": "spd_pct",
    "acc_v": "acc_pct",
    "dec_v": "dec_pct",
}
TARGET_NAMES = {"query_table.json", "flows.json"}


def _pct_value(value: Any, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if numeric <= 150 else default


def migrate_params(params: dict[str, Any]) -> bool:
    changed = False
    for legacy_key, pct_key in LEGACY_KEYS.items():
        if pct_key in params:
            if legacy_key in params:
                params.pop(legacy_key, None)
                changed = True
            continue
        if legacy_key in params:
            params[pct_key] = _pct_value(params.pop(legacy_key), DEFAULTS[pct_key])
            changed = True
    return changed


def walk_json(value: Any) -> bool:
    changed = False
    if isinstance(value, dict):
        params = value.get("params")
        if isinstance(params, dict):
            changed = migrate_params(params) or changed
        changed = migrate_params(value) or changed
        for child in value.values():
            changed = walk_json(child) or changed
    elif isinstance(value, list):
        for item in value:
            changed = walk_json(item) or changed
    return changed


def iter_targets(root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in root.rglob("*.json"):
        if path.name not in TARGET_NAMES:
            continue
        if "exported_logs" in path.parts:
            continue
        paths.append(path)
    return sorted(paths)


def migrate_file(path: Path, *, dry_run: bool) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    changed = walk_json(payload)
    if changed and not dry_run:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate V4.3 speed fields to spd_pct/acc_pct/dec_pct.")
    parser.add_argument("--root", default=".", help="Repository root to scan.")
    parser.add_argument("--dry-run", action="store_true", help="Only print files that would change.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    changed_paths = []
    for path in iter_targets(root):
        if migrate_file(path, dry_run=args.dry_run):
            changed_paths.append(path)

    action = "would migrate" if args.dry_run else "migrated"
    for path in changed_paths:
        print(f"{action}: {path.relative_to(root)}")
    print(f"{action} files: {len(changed_paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
