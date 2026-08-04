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

## Recovered per-user pipe identity

Static disassembly shows that QwenIME calls `GetUserNameW`, walks the UTF-16 code units, preserves
only ASCII letters and digits, replaces every other code unit with `_`, and falls back to `default`
when no username is available. The main process boundary is therefore deterministic:

```text
\\.\pipe\QianwenIME_<sanitized-user>_Pipe
```

The same identity also forms:

```text
\\.\pipe\QianwenIME_<sanitized-user>_ControlPipe
\\.\pipe\QianwenIME_<sanitized-user>_UtilityPipe
```

This removes the earlier uncertainty about a SID or session-derived suffix. It also makes a
pre-launched compatible server preferable to changing the installed executable, provided later
integration tests confirm that the official front end connects to an already-present pipe before
attempting to start its bundled server.

## Recovered main-pipe packet envelope

The main pipe uses Windows named-pipe message semantics. `ReadWindowsIpcMessage` reads 64 KiB
chunks and continues only on `ERROR_MORE_DATA`; `WriteWindowsIpcMessage` writes one complete
serialized message. There is no independent four-byte stream-length prefix.

The statically recovered packet envelope is little-endian:

```text
Offset  Size  Field
0       4     header magic 0x54554951 (bytes "QIUT")
4       2     header version = 1
6       2     header size = 16 for this version
8       4     wire command; bit 31 marks a response
12      4     payload block bytes = JSON bytes + 8
16      4     command ID
20      4     JSON byte count
24      N     UTF-8 JSON payload
24 + N  4     trailer magic 0x45554951 (bytes "QIUE")
```

The parser allows a header larger than 16 bytes for forward compatibility, requires exact total
packet size, caps JSON at `0x500000` bytes, validates both magic values, and checks that the JSON
length agrees with the payload block. The compatibility layer now implements this generic envelope
without yet claiming the full command-ID-to-wire-command table.

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

This evidence is sufficient to justify a normalized bridge core and the recovered generic packet
envelope. It is **not** sufficient to claim that every command mapping, exact JSON field alias, or
UI side channel has been fully recovered.

## Phase delivered by this change

1. Pin the exact tested vendor binaries by size and SHA-256.
2. Define a strict normalized request/response model for the observed functions.
3. Implement a deterministic, replay-testable full-pinyin session bridge.
4. Preserve raw literal input when the model query fails.
5. Refuse missing sessions, invalid candidate indices, unsupported actions, and unknown functions.
6. Enforce fail-closed secure-input isolation.
7. Generate a non-mutating, server-only swap plan that declares zero TSF registration and zero
   registry changes.
8. Reproduce the deterministic per-user pipe names.
9. Encode and decode the recovered generic main-pipe packet envelope.
10. Build and test on Windows and Linux without installing or executing QwenIME.
11. Continuously deliver a source/wheel compatibility artifact containing no vendor binaries.

## Explicitly out of scope

- registering or replacing the system TSF profile;
- writing TSF/TIP or COM registration entries;
- executing the server swap on a daily-use Windows account;
- claiming the current normalized JSON model is the final command-specific payload schema;
- voice input, cloud services, telemetry, settings UI, T9, double pinyin, fuzzy pinyin, or updater
  integration;
- redistributing any QwenIME binary or resource.

## Gate for the next phase

The next static task is to recover the command-ID-to-wire-command table and the minimum required
JSON fields for session creation, key processing, candidate actions, and focus state. Any remaining
unknowns should then be validated in a disposable Windows Sandbox or VM and converted into golden
contract fixtures before a live adapter is enabled.

The eventual server or swap executor must remain fail-closed, version-pinned, reversible, disabled
by default, and limited to the server process boundary; it must not register or unregister the
input method.
