# Experimental TSF profile and Microsoft Pinyin fallback

## Safety boundary

The planner in `native/tsf/` remains read-only. The separate profile tool is the
only mutating boundary; CI invokes it only in dry-run mode and never alters an
installed profile.

The reserved experimental identifiers are:

```text
Text service CLSID: {8AA66261-ED5F-46B0-895D-339B42C3AE1B}
zh-CN profile GUID: {C9B3984E-A16C-4779-80E8-ACD988C57B0D}
Display name:       神经小狼毫（实验）
```

`DefaultExperimentalProfileSpec()` is the only accepted default identity.
`PlanExperimentalProfileRegistration()` and
`PlanExperimentalProfileUnregistration()` inspect an enumeration snapshot and
return one of:

- `kNoOp` when the requested end state is already true;
- `kRegisterExperimentalProfile` when exactly the reserved profile is absent;
- `kUnregisterExperimentalProfile` when exactly that profile is present;
- `kConflict` when only one reserved identifier has been reused.

`NeuralWeaselProfileTool.exe` implements machine-wide COM plus category registration and a per-user profile
registration/unregistration. It refuses any CLSID/profile GUID other than the
reserved pair and verifies the TSF DLL identity exports.

## Microsoft Pinyin selection

Fallback configuration has two supported phases:

1. Enumerate profiles with
   `ITfInputProcessorProfileMgr::EnumProfiles(MAKELANGID(LANG_CHINESE,
   SUBLANG_CHINESE_SIMPLIFIED), ...)`.
2. Present enabled keyboard TIPs whose localized description contains
   `Microsoft Pinyin` or `微软拼音`, then persist the profile identity selected
   by the user.

Persist these fields, not merely the display name:

```text
profile_type, language_id, text_service_clsid, profile_guid
```

At startup, `FindConfiguredProfile()` must resolve the persisted identity
against a fresh enumeration. Discovery is only a convenience for the settings
UI: zero or multiple matches require user intervention, and the runtime must
not silently choose the first profile. Names are localized and are not a
stable identity.

`ActivateEnabledInputProcessorProfile()` accepts only an already-enabled text
service and calls:

```cpp
ITfInputProcessorProfileMgr::ActivateProfile(
    TF_PROFILETYPE_INPUTPROCESSOR,
    language_id,
    text_service_clsid,
    profile_guid,
    nullptr,
    TF_IPPMF_FORSESSION);
```

It deliberately omits `TF_IPPMF_ENABLEPROFILE`, so fallback does not modify
the user's enabled-profile registry state. It also omits
`TF_IPPMF_DONTCARECURRENTINPUTLANGUAGE`: a language mismatch must fail instead
of scheduling a delayed activation. The experimental Neural Weasel CLSID is
rejected as a fallback target.

## Hard-failure state machine

`BackendFallbackStateMachine` uses `std::chrono::steady_clock`, not wall-clock
time. It starts only after `Arm()`.

```text
unarmed
  -> monitoring
       -- heartbeat age > 2000 ms --------------------+
       -- explicit fatal/OOM/pipe-closed event -------+
                                                        v
                              composition active? -> cancel composition
                                                        |
                                                        v
                                               activate fallback
                                                        |
                                      +-----------------+----------------+
                                      v                                  v
                               fallback active                    fallback failed
```

The timeout comparison is strictly greater than 2 seconds. A heartbeat updates
the deadline only while monitoring. Once a hard failure is latched:

- any active Neural Weasel composition must be cancelled first;
- profile activation is forbidden if cancellation did not return `S_OK`;
- `S_FALSE` from `ActivateProfile` is failure because it means the profile is
  disabled;
- later heartbeats are ignored;
- there is no automatic re-arm and no automatic switch-back path.

`DriveFallbackOnce()` executes at most the cancel-then-activate sequence. It
must be invoked on the thread that owns the TSF composition and profile-manager
COM object. A successful model restart may produce a notification, but only an
explicit user action may select Neural Weasel again.

## Integration obligations

The Weasel fork still needs to provide:

- a cancellation callback that confirms the composition is empty before
  returning `S_OK`;
- delivery of `health`, `fatal`, process-exit and OOM signals to the owner
  thread;
- a 2-second watchdog tick that does no pipe or model work on the key path;
- settings UI for explicit fallback-profile approval;
- persistence under `%LOCALAPPDATA%\NeuralWeasel`, never the existing Weasel
  user-data directory;
- diagnostics containing only state, HRESULT and profile GUIDs.

No fallback test should invoke `DriveFallbackOnce()` with a real profile
manager outside an isolated VM/test user. Unit tests should drive the pure
state machine and use a fake activation boundary.

## Remaining manual risks

Windows CI provides compilation evidence. The remaining interactive risks are:

- the selected Windows SDK must expose
  `IID_ITfInputProcessorProfileMgr`, `GUID_TFCAT_TIP_KEYBOARD` and the Vista+
  profile-manager flags used here;
- `ITfInputProcessorProfileMgr` must successfully query
  `ITfInputProcessorProfiles` to obtain localized descriptions on supported
  Windows versions; empty descriptions remain valid but cannot be auto-
  discovered;
- activation and composition cancellation must occur on the correct apartment
  and TSF owner thread;
- profile descriptions vary by Windows display language, so discovery aliases
  need verification on the user's installation;
- profile visibility, DLL unload, and unregister cleanup must be verified in a
  disposable Windows user or VM.

The next verification step is the isolated manual smoke test, including
read-only diagnosis before registration and complete removal afterward.
