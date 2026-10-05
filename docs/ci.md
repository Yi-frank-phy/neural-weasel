# Continuous integration

Reviewed against `.github/workflows/ci.yml` on 2026-10-05. The main workflow
has two Windows validation jobs and a tag-gated release job. Other workflows
check the one-click launcher and QwenIME compatibility slice.

`python` runs the repository's CPU-only unit tests on `windows-latest` with
Python 3.12.

It deliberately does not exercise the model runtime:

- `uv sync --extra dev --no-install-package torch` installs the project and test
  dependencies without downloading PyTorch.
- `uv run --no-sync` prevents the lint and test commands from implicitly syncing
  the omitted Torch package.
- Tensor-specific test modules use `pytest.importorskip("torch")`; they
  run in a full local development environment and are reported as explicit skips in
  this lightweight job.
- Hugging Face Hub and Transformers offline modes are enabled.
- `%LOCALAPPDATA%` and `NEURAL_WEASEL_HOME` point at runner-temporary directories,
  so CI cannot read a developer's model cache, generated indexes, logs, or private
  context.
- GPU/model integration tests remain a local, explicitly invoked validation step.

`windows-vertical-slice` runs on `windows-2022`:

- checks out pinned Weasel `0.17.4` at
  `9cc96e20dc71b80876b12f689bb5863c76c2a7ed` and fixed librime `1.15.0` boundary headers;
- installs `nlohmann-json`, Boost Signals2, and Boost Unordered through the runner's
  vcpkg;
- configures with both `NEURAL_WEASEL_BUILD_NATIVE_TESTS=ON` and
  `NEURAL_WEASEL_BUILD_RIME_PLUGIN=ON`;
- compiles the Windows pipe/context/profile boundaries and the static translator/key
  processor;
- runs the native CTest state-machine tests.
- applies the isolated Weasel overlay and builds the experimental TSF DLL,
  server and static neural module;
- assembles a hashed bundle, verifies binary/resource identities, and runs
  fail-closed install/uninstall and one-click-launcher dry runs;
- uploads `neural-weasel-experimental-x64` after successful validation.

The job produces the independent experimental profile bundle. It does not
register a global profile on the shared runner or prove real editor behavior.

`release` depends on both validation jobs and runs only for tags starting with
`v` and containing `-experimental.`. It checks commit provenance, prepares
attachments and uploads them before publishing the experimental prerelease.
Test counts and run outcomes belong to the exact commit/run, not this overview.

Run the same checks locally after a development environment has already been
created:

```powershell
uv run --no-sync ruff check .
uv run --no-sync pytest
```
