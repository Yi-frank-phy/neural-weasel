from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EXPERIMENTAL_CLSID = "{8AA66261-ED5F-46B0-895D-339B42C3AE1B}"
EXPERIMENTAL_PROFILE_GUID = "{C9B3984E-A16C-4779-80E8-ACD988C57B0D}"
EXPERIMENTAL_DISPLAY_NAME = "神经小狼毫（实验）"


@dataclass(frozen=True, slots=True)
class ProfileInstallManifest:
    clsid: str
    profile_guid: str
    display_name: str
    install_directory: Path
    set_default: bool


def validate_experimental_manifest(manifest: ProfileInstallManifest) -> None:
    if (
        manifest.clsid.upper() != EXPERIMENTAL_CLSID
        or manifest.profile_guid.upper() != EXPERIMENTAL_PROFILE_GUID
        or manifest.display_name != EXPERIMENTAL_DISPLAY_NAME
    ):
        raise ValueError("manifest does not target the reserved experimental profile")
    if manifest.set_default:
        raise ValueError("experimental profile must not be activated as default")
    if "experimental-profile" not in {
        component.casefold() for component in manifest.install_directory.parts
    }:
        raise ValueError("install directory must contain an experimental-profile boundary")
