# Windows experimental install smoke test

This procedure is for **Windows Sandbox, a disposable VM, or a dedicated test
user only**. Neural Weasel is experimental and is not production ready. CI
builds and statically verifies the bundle, but it does not register a global
TSF profile or prove real typing behavior in an interactive Windows session.

## Preconditions

- 64-bit Windows 11 with PowerShell 7.
- A disposable account that can open Windows language/input settings.
- `uv` installed and available on `PATH`.
- Enough disk and network access to download the configured Base checkpoint.
- If official Weasel is installed, record its profile availability before
  starting. Do the same for Microsoft Pinyin.

Do not run the test from an official Weasel installation directory. Do not
change the system default input method.

## 1. Download and inspect the CI artifact

1. Download the `neural-weasel-experimental-x64` artifact from the successful
   GitHub Actions run for the tested commit.
2. Extract it to a new directory owned by the disposable user.
3. Open PowerShell in the extracted
   `neural-weasel-experimental` directory.
4. Confirm that `build-manifest.json` names the expected commit and contains
   SHA-256 hashes for the bundle.
5. Run:

   ```powershell
   .\diagnose.ps1
   .\install-dev-profile.ps1 -DryRun
   ```

The dry run must report success and must not add an input method. Diagnose may
report that the profile, pipe, and service are absent before installation.

## 2. Install the isolated profile

Run:

```powershell
.\install-dev-profile.ps1
.\diagnose.ps1
```

Expected results:

- the display name is `神经小狼毫（实验）`;
- installation is under
  `%LOCALAPPDATA%\NeuralWeasel\Experimental\experimental-profile`;
- the default input method does not change;
- official Weasel and Microsoft Pinyin remain present and unchanged.

Open the Windows input switcher with `Win+Space` and manually select
`神经小狼毫（实验）`. If it is absent, stop and collect the non-sensitive
diagnostic output; do not attempt to register another DLL or GUID.

## 3. Start the model service

From the installed directory, start the correctness baseline:

```powershell
.\start-model-service.ps1 -Backend full
```

Leave that PowerShell window open. A first run may build the pinyin index and
download the Base checkpoint. Failure must be explicit; the script must not
silently choose another model or backend.

The optional sparse path can be tested separately:

```powershell
.\start-model-service.ps1 -Backend sparse
```

An unsupported or failed sparse initialization is a valid explicit failure,
not permission to fall back to `full` or a different checkpoint.

## 4. Exercise Chinese input

In Notepad under the experimental profile:

1. Type a continuous full-pinyin sequence such as `nihao`.
2. Confirm character input and candidate display.
3. Use Backspace and confirm the composition updates.
4. Press Space and confirm the selected Han candidate is committed.
5. Repeat and use a numbered candidate key from `1` through `9`.
6. Press Enter on a composition and confirm the literal composition commits.
7. Press Escape on a composition and confirm it is cancelled.

Do not test fuzzy pinyin, double pinyin, abbreviated pinyin, tones, or typo
correction; they are outside this slice.

## 5. Exercise English input

With an English/Latin candidate visible:

1. Type a literal prefix and confirm the literal remains visible.
2. Press Space. It must commit the literal prefix followed by one space; it
   must never accept top-1 completion.
3. Type another prefix and press Tab. Tab may explicitly accept the selected
   completion.
4. Press Escape. The completion must close while the literal prefix remains.
5. Press Enter. The literal prefix must commit and the editor must still
   receive its normal Enter behavior.
6. Use Backspace and confirm the literal updates.
7. Use number or direction keys and confirm they do not silently replace the
   literal with a stale/unselected completion.

This slice provides only the current single-token live baseline. It does not
claim complete multi-token causal rescoring.

## 6. Verify safe degradation

1. Stop the model-service PowerShell process.
2. Continue typing Chinese and English text.
3. Confirm the editor remains responsive, the input method process does not
   crash, and literal input can still be committed.
4. Restart the experimental server if needed, reselect the profile, and repeat
   a short input test.
5. Test a password or other secure field. No AI candidate should replace the
   literal input, and diagnostics/logs must not contain the field contents.

Also test service absence before profile selection and a service restart during
composition. Any timeout, malformed response, stale response, or empty
candidate response must preserve literal input.

## 7. Uninstall and verify isolation

Run:

```powershell
.\uninstall-dev-profile.ps1 -DryRun
.\uninstall-dev-profile.ps1
.\uninstall-dev-profile.ps1
.\diagnose.ps1
```

The second real uninstall is an idempotence check. Confirm:

- `神经小狼毫（实验）` is no longer in the input switcher;
- the experimental server is not running;
- the experimental install directory is gone;
- official Weasel still works if it existed before the test;
- Microsoft Pinyin still works;
- no default input method was changed.

Model weights are preserved by default. Remove them only with the explicit
`-RemoveModelCache` switch if the test environment should be cleaned fully.

## 8. Collect safe evidence

Save only:

- the CI run URL and artifact name;
- the tested repository commit from `build-manifest.json`;
- redacted `diagnose.ps1` JSON;
- pass/fail notes for each numbered section;
- non-sensitive crash/event identifiers if a failure occurred.

Do not collect typed text, candidate text from private documents, surrounding
context, password-field contents, model prompts, or private window titles.

## CI versus manual evidence

CI verifies compilation, unit/contract tests, bundle hashes, dry-run install
safety, and identity scanning. Sections 2 through 7 above—global TSF
registration, visibility in `Win+Space`, interactive Chinese/English typing,
secure-field behavior, process restart behavior, and complete removal—remain
manual until a dated, auditable Windows Sandbox/VM report is attached to the
release or pull request.
