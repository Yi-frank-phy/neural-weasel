#pragma once

#include <windows.h>

#include <cstdio>

namespace neural_weasel::tsf {

// Candidate UI diagnostics must remain text-free: never record composition,
// surrounding text, candidate strings, window titles, or document metadata.
inline void WriteCandidateUiDiagnostic(const char* event,
                                       HRESULT begin_hr,
                                       BOOL pb_show,
                                       bool ui_started,
                                       bool create_attempted,
                                       bool create_success,
                                       bool shown,
                                       HWND hwnd) {
  if (event == nullptr) {
    return;
  }

  const bool is_window = hwnd != nullptr && IsWindow(hwnd) != FALSE;
  RECT rect = {};
  const bool has_rect = is_window && GetWindowRect(hwnd, &rect) != FALSE;
  const LONG_PTR style = is_window ? GetWindowLongPtrW(hwnd, GWL_STYLE) : 0;
  const LONG_PTR ex_style = is_window ? GetWindowLongPtrW(hwnd, GWL_EXSTYLE) : 0;
  const HMONITOR monitor = is_window ? MonitorFromWindow(hwnd, MONITOR_DEFAULTTONULL)
                                     : nullptr;

  wchar_t local_app_data[32768] = {};
  const DWORD local_app_data_size = GetEnvironmentVariableW(
      L"LOCALAPPDATA", local_app_data, static_cast<DWORD>(_countof(local_app_data)));
  if (local_app_data_size == 0 ||
      local_app_data_size >= static_cast<DWORD>(_countof(local_app_data))) {
    return;
  }

  wchar_t neural_root[32768] = {};
  wchar_t experimental_root[32768] = {};
  wchar_t log_root[32768] = {};
  wchar_t log_path[32768] = {};
  if (swprintf_s(neural_root, _countof(neural_root), L"%s\\NeuralWeasel",
                 local_app_data) < 0 ||
      swprintf_s(experimental_root, _countof(experimental_root),
                 L"%s\\Experimental", neural_root) < 0 ||
      swprintf_s(log_root, _countof(log_root), L"%s\\Logs",
                 experimental_root) < 0 ||
      swprintf_s(log_path, _countof(log_path),
                 L"%s\\candidate-ui-events.log", log_root) < 0) {
    return;
  }

  CreateDirectoryW(neural_root, nullptr);
  CreateDirectoryW(experimental_root, nullptr);
  CreateDirectoryW(log_root, nullptr);

  HANDLE file = CreateFileW(log_path, FILE_APPEND_DATA,
                            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                            nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
  if (file == INVALID_HANDLE_VALUE) {
    return;
  }

  char line[1024] = {};
  const int line_size = sprintf_s(
      line, sizeof(line),
      "tick=%llu pid=%lu tid=%lu event=%s begin_hr=0x%08lx pb_show=%d "
      "ui_started=%d create_attempted=%d create_success=%d shown=%d "
      "hwnd=%p is_window=%d has_rect=%d rect=%ld,%ld,%ld,%ld "
      "style=0x%llx ex_style=0x%llx monitor=%p\r\n",
      static_cast<unsigned long long>(GetTickCount64()),
      static_cast<unsigned long>(GetCurrentProcessId()),
      static_cast<unsigned long>(GetCurrentThreadId()), event,
      static_cast<unsigned long>(begin_hr), pb_show ? 1 : 0,
      ui_started ? 1 : 0, create_attempted ? 1 : 0,
      create_success ? 1 : 0, shown ? 1 : 0, hwnd, is_window ? 1 : 0,
      has_rect ? 1 : 0, static_cast<long>(rect.left),
      static_cast<long>(rect.top), static_cast<long>(rect.right),
      static_cast<long>(rect.bottom), static_cast<unsigned long long>(style),
      static_cast<unsigned long long>(ex_style), monitor);
  if (line_size > 0) {
    DWORD written = 0;
    WriteFile(file, line, static_cast<DWORD>(line_size), &written, nullptr);
  }
  CloseHandle(file);
}

}  // namespace neural_weasel::tsf
