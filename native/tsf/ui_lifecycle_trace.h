#pragma once

#include <windows.h>

#include <cstdarg>

namespace neural_weasel::tsf {

// Metadata-only diagnostics for the experimental TSF candidate UI. Never pass
// key codes, candidate text, surrounding text, window titles, or capability
// values to this function.
inline void TraceUiLifecycle(const wchar_t* format, ...) {
  wchar_t local_app_data[MAX_PATH] = {};
  DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", local_app_data,
                                         _countof(local_app_data));
  if (length == 0 || length >= _countof(local_app_data)) {
    return;
  }

  wchar_t path[MAX_PATH] = {};
  if (swprintf_s(path, L"%s\\NeuralWeasel\\Experimental\\ui-lifecycle.log",
                 local_app_data) < 0) {
    return;
  }

  HANDLE file = CreateFileW(path, FILE_APPEND_DATA,
                            FILE_SHARE_READ | FILE_SHARE_WRITE |
                                FILE_SHARE_DELETE,
                            nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL,
                            nullptr);
  if (file == INVALID_HANDLE_VALUE) {
    return;
  }

  wchar_t message[768] = {};
  va_list args;
  va_start(args, format);
  const int chars = _vsnwprintf_s(message, _countof(message), _TRUNCATE,
                                  format, args);
  va_end(args);
  if (chars < 0) {
    CloseHandle(file);
    return;
  }

  wchar_t line[1024] = {};
  const int line_chars = swprintf_s(
      line, L"tick=%llu pid=%lu tid=%lu %s\r\n", GetTickCount64(),
      GetCurrentProcessId(), GetCurrentThreadId(), message);
  if (line_chars > 0) {
    DWORD written = 0;
    WriteFile(file, line,
              static_cast<DWORD>(line_chars * sizeof(wchar_t)), &written,
              nullptr);
  }
  CloseHandle(file);
}

}  // namespace neural_weasel::tsf
