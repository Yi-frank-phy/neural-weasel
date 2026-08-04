from __future__ import annotations

import argparse
import json
from pathlib import Path

from .manifest import verify_vendor_install


def main() -> int:
    parser = argparse.ArgumentParser(description="Static QwenIME compatibility checks")
    parser.add_argument("install_root", type=Path)
    args = parser.parse_args()

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
