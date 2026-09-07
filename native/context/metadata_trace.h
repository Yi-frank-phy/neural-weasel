#pragma once

#include <Windows.h>

#include <cstdarg>
#include <cstdio>

namespace neural_weasel::context {

// Temporary target-machine diagnostic for the editor-context pipeline.
// Callers must pass metadata only: never raw keys, candidate/editor text,
// capabilities, window titles, or application paths.
inline void TraceContextPipeline(const wchar_t* component,
                                 const wchar_t* format,
                                 ...) noexcept {
  wchar_t local_app_data[MAX_PATH] = {};
  const DWORD length = GetEnvironmentVariableW(
      L"LOCALAPPDATA", local_app_data, _countof(local_app_data));
  if (length == 0 || length >= _countof(local_app_data)) {
    return;
  }

  wchar_t path[MAX_PATH] = {};
  if (swprintf_s(path,
                 L"%s\\NeuralWeasel\\Experimental\\context-pipeline.log",
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
  if (chars >= 0) {
    wchar_t line[1024] = {};
    const int line_chars = swprintf_s(
        line, L"tick=%llu pid=%lu tid=%lu component=%s %s\r\n",
        GetTickCount64(), GetCurrentProcessId(), GetCurrentThreadId(),
        component, message);
    if (line_chars > 0) {
      DWORD written = 0;
      WriteFile(file, line,
                static_cast<DWORD>(line_chars * sizeof(wchar_t)), &written,
                nullptr);
    }
  }
  CloseHandle(file);
}

}  // namespace neural_weasel::context
