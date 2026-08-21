from __future__ import annotations

from dataclasses import dataclass, replace

FAST_BEFORE_UTF16 = 8192
FAST_AFTER_UTF16 = 4096
IDLE_BEFORE_UTF16 = 32768
IDLE_AFTER_UTF16 = 32768


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _take_utf16_prefix(text: str, maximum_units: int) -> str:
    if maximum_units < 0:
        raise ValueError("maximum_units must be non-negative")
    encoded = text.encode("utf-16-le")
    clipped = encoded[: maximum_units * 2]
    if len(clipped) >= 2:
        last_unit = int.from_bytes(clipped[-2:], "little")
        if 0xD800 <= last_unit <= 0xDBFF:
            clipped = clipped[:-2]
    return clipped.decode("utf-16-le", errors="strict")


def _take_utf16_suffix(text: str, maximum_units: int) -> str:
    reversed_text = text[::-1]
    return _take_utf16_prefix(reversed_text, maximum_units)[::-1]


@dataclass(frozen=True, slots=True)
class EditorContext:
    before: str
    after: str
    app_id: str
    partial: bool
    complete_region: bool
    secure: bool
    capture_hresult: int = 0

    @classmethod
    def secure_context(cls, app_id: str, capture_hresult: int = 0) -> EditorContext:
        return cls(
            before="",
            after="",
            app_id=app_id,
            partial=True,
            complete_region=False,
            secure=True,
            capture_hresult=capture_hresult,
        )

    def clipped_fast(self) -> EditorContext:
        if self.secure:
            return replace(self, before="", after="")
        return replace(
            self,
            before=_take_utf16_suffix(self.before, FAST_BEFORE_UTF16),
            after=_take_utf16_prefix(self.after, FAST_AFTER_UTF16),
        )

    def clipped_idle(self) -> EditorContext:
        if self.secure:
            return replace(self, before="", after="")
        return replace(
            self,
            before=_take_utf16_suffix(self.before, IDLE_BEFORE_UTF16),
            after=_take_utf16_prefix(self.after, IDLE_AFTER_UTF16),
        )

    def metadata(self) -> dict[str, object]:
        """Return log-safe metadata. This method never returns context text."""
        return {
            "app_id": self.app_id,
            "before_utf16": _utf16_units(self.before),
            "after_utf16": _utf16_units(self.after),
            "scope_label": "PASSWORD" if self.secure else "NORMAL",
        }


SECURE_INPUT_SCOPES = frozenset(
    {
        "IS_PASSWORD",
        "IS_PASSWORD_PIN",
        "IS_PIN_NUMERIC",
        "IS_PIN_ALPHANUMERIC",
    }
)


def is_secure_capture(
    *,
    input_scopes: set[str] | frozenset[str],
    app_id: str,
    blacklisted_apps: set[str] | frozenset[str],
    secure_desktop: bool,
    protected_field: bool,
) -> bool:
    """Fail closed when any independent security signal says not to capture."""
    normalized_app = app_id.casefold()
    normalized_blacklist = {item.casefold() for item in blacklisted_apps}
    return (
        secure_desktop
        or protected_field
        or normalized_app in normalized_blacklist
        or bool(SECURE_INPUT_SCOPES.intersection(input_scopes))
    )
