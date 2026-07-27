# Continuous integration

The GitHub Actions workflow has two Windows jobs.

`python` runs the repository's CPU-only unit tests on `windows-latest` with
Python 3.12.

It deliberately does not exercise the model runtime:

- `uv sync --extra dev --no-install-package torch` installs the project and test
  dependencies without downloading PyTorch.
- `uv run --no-sync` prevents the lint and test commands from implicitly syncing
  the omitted Torch package.
- Hugging Face Hub and Transformers offline modes are enabled.
- `%LOCALAPPDATA%` and `NEURAL_WEASEL_HOME` point at runner-temporary directories,
  so CI cannot read a developer's model cache, generated indexes, logs, or private
  context.
- GPU/model integration tests remain a local, explicitly invoked validation step.

`native-windows`:

- checks out fixed librime `1.15.0`;
- installs `nlohmann-json` through the runner's vcpkg;
- configures with both `NEURAL_WEASEL_BUILD_NATIVE_TESTS=ON` and
  `NEURAL_WEASEL_BUILD_RIME_PLUGIN=ON`;
- compiles the Windows pipe/context/profile boundaries and the static translator/key
  processor;
- runs the native CTest state-machine tests.

This job does not produce or install an independent Weasel TSF profile. It verifies
that the repository-owned native boundaries compile against the declared upstream
API.

Run the same checks locally after a development environment has already been
created:

```powershell
uv run --no-sync ruff check .
uv run --no-sync pytest
```
