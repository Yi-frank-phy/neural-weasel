from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

EXPERIMENTAL_CLSID = "{8AA66261-ED5F-46B0-895D-339B42C3AE1B}"
EXPERIMENTAL_PROFILE = "{C9B3984E-A16C-4779-80E8-ACD988C57B0D}"
OFFICIAL_CLSID = "{A3F4CDED-B1E9-41EE-9CA6-7B4D0DE6CB0A}"
OFFICIAL_PROFILE = "{3D02CAB6-2B8E-4781-BA20-1C9267529467}"

TSF_FORBIDDEN_RUNTIME_LITERALS = (
    "NeuralWeasel-v1-",
    '"context_update"',
    "query_candidates",
)

REQUIRED = (
    "NeuralWeaselExperimentalTSF.dll",
    "NeuralWeaselProfileTool.exe",
    "NeuralWeaselSessionActivator.exe",
    "NeuralWeaselServer.exe",
    "NeuralWeaselRimeModule.lib",
    "install-dev-profile.ps1",
    "uninstall-dev-profile.ps1",
    "diagnose.ps1",
    "start-model-service.ps1",
    "launch-neural-weasel.ps1",
    "Start-Neural-Weasel.cmd",
    "启动神经小狼毫.cmd",
    "tools/uv.exe",
    "build-manifest.json",
    "README-INSTALL-TEST.md",
    "data/neural_weasel.schema.yaml",
    "python-service/README.md",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _encodings(value: str) -> tuple[bytes, bytes]:
    return value.encode("utf-8"), value.encode("utf-16-le")


def verify(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED:
        if not (root / relative).is_file():
            errors.append(f"missing required artifact: {relative}")

    manifest_path = root / "build-manifest.json"
    if not manifest_path.is_file():
        return errors
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        return [*errors, f"invalid build manifest: {error}"]

    expected_manifest = {
        "experimental_clsid": EXPERIMENTAL_CLSID,
        "experimental_profile_guid": EXPERIMENTAL_PROFILE,
        "architecture": "x64",
        "uv_version": "uv 0.8.22",
    }
    for field, expected in expected_manifest.items():
        if manifest.get(field) != expected:
            errors.append(f"manifest {field} is not the reserved value")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        errors.append("manifest artifacts map is empty")
    else:
        for relative, expected_hash in artifacts.items():
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                errors.append(f"manifest path escapes bundle: {relative}")
                continue
            path = root / relative_path
            if not path.is_file():
                errors.append(f"manifest path is missing: {relative}")
            elif _digest(path) != str(expected_hash).casefold():
                errors.append(f"manifest hash mismatch: {relative}")

    scan_paths = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".dll", ".exe", ".lib", ".yaml", ".json"}
    ]
    forbidden_literals = (
        OFFICIAL_CLSID,
        OFFICIAL_PROFILE,
        "WeaselNamedPipe",
        "WeaselIPCWindow_1.0",
        "WeaselTSF Button",
        r"Software\Rime\Weasel",
    )
    for path in scan_paths:
        data = path.read_bytes()
        for literal in forbidden_literals:
            if any(encoded in data for encoded in _encodings(literal)):
                errors.append(f"official identity {literal!r} remains in {path.relative_to(root)}")
        if re.search(rb"(?<!Neural)WeaselServer\.exe", data):
            errors.append(f"official server launch name remains in {path.relative_to(root)}")
        if re.search(
            "WeaselServer\\.exe".encode("utf-16-le"),
            data.replace("NeuralWeaselServer.exe".encode("utf-16-le"), b""),
        ):
            errors.append(f"official UTF-16 server name remains in {path.relative_to(root)}")
        if path.suffix.casefold() in {".dll", ".exe", ".lib"}:
            for identity in (OFFICIAL_CLSID, OFFICIAL_PROFILE):
                if uuid.UUID(identity).bytes_le in data:
                    errors.append(
                        f"official binary GUID {identity} remains in {path.relative_to(root)}"
                    )

    tsf_path = root / "NeuralWeaselExperimentalTSF.dll"
    if tsf_path.is_file():
        tsf_data = tsf_path.read_bytes()
        for literal in TSF_FORBIDDEN_RUNTIME_LITERALS:
            if any(encoded in tsf_data for encoded in _encodings(literal)):
                errors.append(
                    "in-process TSF contains neural runtime "
                    f"marker {literal!r}; crash containment is broken"
                )

    identity_targets = (
        root / "NeuralWeaselExperimentalTSF.dll",
        root / "NeuralWeaselProfileTool.exe",
        root / "NeuralWeaselSessionActivator.exe",
    )
    for target in identity_targets:
        if not target.is_file():
            continue
        data = target.read_bytes()
        for identity in (EXPERIMENTAL_CLSID, EXPERIMENTAL_PROFILE):
            if not (
                any(encoded in data for encoded in _encodings(identity))
                and uuid.UUID(identity).bytes_le in data
            ):
                errors.append(f"{target.name} does not embed experimental identity {identity}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    errors = verify(args.bundle.resolve())
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"Verified isolated Windows bundle: {args.bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
