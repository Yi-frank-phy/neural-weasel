# QwenIME 0.5.0.7 compatibility boundary

## Goal

Reuse the official QwenIME TSF and candidate-window processes while keeping Neural Weasel in a
separate fail-closed process. No vendor executable, DLL, PDB, model, dictionary, or user data is
committed or redistributed.

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
6. Build and test on Windows and Linux without installing or executing QwenIME.
7. Continuously deliver a source/wheel compatibility artifact containing no vendor binaries.

## Explicitly out of scope

- registering or replacing the system TSF profile;
- replacing `QianwenIMEServer.exe` on a daily-use Windows account;
- claiming the current JSON model is the final on-wire packet codec;
- voice input, cloud services, telemetry, settings UI, T9, double pinyin, fuzzy pinyin, or updater
  integration;
- redistributing any QwenIME binary or resource.

## Gate for the next phase

A later change may implement the actual Windows pipe server only after a disposable Windows
Sandbox or VM produces redacted request/response fixtures for every required function. Those
fixtures must become golden contract tests before any live adapter is enabled. The install or swap
script must remain fail-closed, version-pinned, reversible, and disabled by default.
