# QwenIME 0.5.0.7 compatibility boundary

## Goal

Reuse the official QwenIME TSF and candidate-window processes while keeping Neural Weasel in a
separate fail-closed process. No vendor executable, DLL, PDB, model, dictionary, or user data is
committed or redistributed.

## Why this path requires no IME re-registration

Windows registers the QwenIME TSF front end, its COM class, language profile, and display identity.
Those objects belong to the existing `qianwenime.dll`. Replacing only the out-of-process
`QianwenIMEServer.exe` does not create a new input method and therefore must not call
`DllRegisterServer`, `regsvr32`, modify the TSF/TIP registry trees, or add another language profile.

The compatibility architecture preserves:

- the installed and registered `qianwenime.dll`;
- the existing QwenIME CLSID and language-profile GUIDs;
- the existing Windows input-method selection entry;
- `QianwenIMEUiClient.exe` and `qime.dll` unless later evidence proves they are not needed.

Only the server process boundary is a candidate for version-pinned, reversible substitution. The
current implementation can generate and inspect that file-only plan, but it does not execute it.

Neural Weasel's original standalone TSF build is different: it defines a new input-method profile,
so its first installation necessarily requires registration. That registration is not part of the
QwenIME-shell compatibility path.

## Evidence boundary

The user-supplied 0.5.0.7 installation was inspected statically only. It contains an official TSF
front end, an out-of-process `QianwenIMEServer.exe`, a separate UI client, a named-pipe prefix,
JSON request/response codec symbols, and the following observed function names:

- `start_session`
- `end_session`
- `focus_in` / `focus_out`
- `update_input_position`
- `process_key`
- `candidate_action`
- `drain_candidate_action_result`
- `cancel_composition`

This evidence is sufficient to justify a normalized bridge core. It is **not** sufficient to claim
that the proprietary packet header, pipe suffix, field aliases, or UI side channel have been fully
recovered.

## Phase delivered by this change

1. Pin the exact tested vendor binaries by size and SHA-256.
2. Define a strict normalized request/response model for the observed functions.
3. Implement a deterministic, replay-testable full-pinyin session bridge.
4. Preserve raw literal input when the model query fails.
5. Refuse missing sessions, invalid candidate indices, unsupported actions, and unknown functions.
6. Enforce fail-closed secure-input isolation.
7. Generate a non-mutating, server-only swap plan that declares zero TSF registration and zero
   registry changes.
8. Build and test on Windows and Linux without installing or executing QwenIME.
9. Continuously deliver a source/wheel compatibility artifact containing no vendor binaries.

## Explicitly out of scope

- registering or replacing the system TSF profile;
- writing TSF/TIP or COM registration entries;
- executing the server swap on a daily-use Windows account;
- claiming the current JSON model is the final on-wire packet codec;
- voice input, cloud services, telemetry, settings UI, T9, double pinyin, fuzzy pinyin, or updater
  integration;
- redistributing any QwenIME binary or resource.

## Gate for the next phase

A later change may implement the actual Windows pipe server only after a disposable Windows
Sandbox or VM produces redacted request/response fixtures for every required function. Those
fixtures must become golden contract tests before any live adapter is enabled. The eventual swap
executor must remain fail-closed, version-pinned, reversible, disabled by default, and limited to
server-process files; it must not register or unregister the input method.
