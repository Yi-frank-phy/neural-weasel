from __future__ import annotations

import argparse
import json
from pathlib import Path

from .manifest import verify_vendor_install
from .swap_plan import QwenImeSwapPlanError, build_server_swap_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Static QwenIME compatibility checks")
    parser.add_argument("install_root", type=Path)
    parser.add_argument(
        "--replacement-server",
        type=Path,
        help="Print a zero-registration server-only swap plan without modifying files",
    )
    args = parser.parse_args()

    if args.replacement_server is not None:
        try:
            plan = build_server_swap_plan(args.install_root, args.replacement_server)
        except QwenImeSwapPlanError as error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        print(json.dumps({"ok": True, "plan": plan.to_dict()}, ensure_ascii=False, indent=2))
        return 0

    report = verify_vendor_install(args.install_root)
    print(
        json.dumps(
            {
                "ok": report.ok,
                "version": report.version,
                "root": str(report.root),
                "mismatches": [
                    {
                        "relative_path": mismatch.relative_path,
                        "reason": mismatch.reason,
                    }
                    for mismatch in report.mismatches
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
