# Native integration skeleton

This directory contains deliberately small native boundaries for the
experimental neural Weasel profile:

- `tsf/`: bounded read-only TSF surrounding-text capture;
- `pipe/`: persistent, deadline-bounded Named Pipe client;
- `rime/`: a librime translator module that converts cached service responses
  into Rime candidates.

The default CMake build does **not** register a TSF profile, install Weasel, or
modify the user's Rime data. The librime module is opt-in because it must be
built against the exact headers shipped with the selected Weasel build.

Example configuration after installing a Visual Studio C++ toolchain:

```powershell
cmake -S native -B build/native -A x64
cmake --build build/native --config RelWithDebInfo
```

To compile the static translator integration library, additionally provide a
matching librime source checkout and a CMake package for `nlohmann_json`:

```powershell
cmake -S native -B build/native -A x64 `
  -DNEURAL_WEASEL_BUILD_RIME_PLUGIN=ON `
  -DRIME_ROOT=C:/src/librime
```

See `docs/architecture/native-integration.md` before wiring these targets into
Weasel. In particular, stock librime `1.15.0` cannot load external plugin DLLs
on Windows; the resulting static library must be linked into the experimental
Weasel build and explicitly loaded. The current files are an integration
skeleton, not an installer.

