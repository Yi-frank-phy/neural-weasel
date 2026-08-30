#pragma once

#include <windows.h>

#include <cstdio>

namespace neural_weasel::tsf {

// Style diagnostics are deliberately text-free. They record only numeric
// paint inputs already present in UIStyle so runtime alpha/config precedence
// can be distinguished from HWND/TSF failures.
inline void WriteCandidateStyleDiagnostic(const char* event,
                                          COLORREF text_color,
                                          COLORREF back_color,
                                          COLORREF candidate_text_color,
                                          COLORREF candidate_back_color,
                                          COLORREF border_color,
                                          COLORREF hilited_candidate_text_color,
                                          COLORREF hilited_candidate_back_color) {
  if (event == nullptr) {
    return;
  }

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

  const auto alpha = [](COLORREF color) -> unsigned {
    return static_cast<unsigned>((color >> 24) & 0xff);
  };

  char line[1024] = {};
  const int line_size = sprintf_s(
      line, sizeof(line),
      "tick=%llu pid=%lu tid=%lu event=style-%s "
      "text_color=0x%08lx text_alpha=%u back_color=0x%08lx back_alpha=%u "
      "candidate_text_color=0x%08lx candidate_text_alpha=%u "
      "candidate_back_color=0x%08lx candidate_back_alpha=%u "
      "border_color=0x%08lx border_alpha=%u "
      "hilited_candidate_text_color=0x%08lx hilited_candidate_text_alpha=%u "
      "hilited_candidate_back_color=0x%08lx hilited_candidate_back_alpha=%u\r\n",
      static_cast<unsigned long long>(GetTickCount64()),
      static_cast<unsigned long>(GetCurrentProcessId()),
      static_cast<unsigned long>(GetCurrentThreadId()), event,
      static_cast<unsigned long>(text_color), alpha(text_color),
      static_cast<unsigned long>(back_color), alpha(back_color),
      static_cast<unsigned long>(candidate_text_color), alpha(candidate_text_color),
      static_cast<unsigned long>(candidate_back_color), alpha(candidate_back_color),
      static_cast<unsigned long>(border_color), alpha(border_color),
      static_cast<unsigned long>(hilited_candidate_text_color),
      alpha(hilited_candidate_text_color),
      static_cast<unsigned long>(hilited_candidate_back_color),
      alpha(hilited_candidate_back_color));
  if (line_size > 0) {
    DWORD written = 0;
    WriteFile(file, line, static_cast<DWORD>(line_size), &written, nullptr);
  }
  CloseHandle(file);
}

}  // namespace neural_weasel::tsf
