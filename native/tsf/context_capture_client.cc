#include "tsf/context_capture_client.h"

#include <sddl.h>

#include <filesystem>
#include <string>
#include <utility>

#include "context/metadata_trace.h"

namespace neural_weasel::tsf {
namespace {

constexpr wchar_t kContextPipePrefix[] =
    L"\\\\.\\pipe\\NeuralWeaselContext-v1-";
constexpr wchar_t kExpectedServerName[] = L"NeuralWeaselServer.exe";
int kModuleAnchor = 0;

std::wstring CurrentUserSid() noexcept {
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
    return {};
  }

  DWORD required = 0;
  GetTokenInformation(token, TokenUser, nullptr, 0, &required);
  if (required == 0) {
    CloseHandle(token);
    return {};
  }
  std::vector<std::uint8_t> buffer(required);
  if (!GetTokenInformation(
          token, TokenUser, buffer.data(), required, &required)) {
    CloseHandle(token);
    return {};
  }
  CloseHandle(token);

  const auto* user = reinterpret_cast<const TOKEN_USER*>(buffer.data());
  LPWSTR sid = nullptr;
  if (!ConvertSidToStringSidW(user->User.Sid, &sid) || sid == nullptr) {
    return {};
  }
  std::wstring result(sid);
  LocalFree(sid);
  return result;
}

std::wstring ContextPipeName() noexcept {
  const std::wstring sid = CurrentUserSid();
  if (sid.empty()) {
    return {};
  }
  return std::wstring(kContextPipePrefix) + sid;
}

std::wstring ModuleDirectory() noexcept {
  HMODULE module = nullptr;
  if (!GetModuleHandleExW(
          GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
              GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
          reinterpret_cast<LPCWSTR>(&kModuleAnchor), &module)) {
    return {};
  }
  std::wstring path(32768, L'\0');
  const DWORD length =
      GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
  if (length == 0 || length >= path.size()) {
    return {};
  }
  path.resize(length);
  try {
    return std::filesystem::path(path).parent_path().wstring();
  } catch (...) {
    return {};
  }
}

bool SamePath(std::wstring_view left, std::wstring_view right) noexcept {
  return CompareStringOrdinal(
             left.data(), static_cast<int>(left.size()), right.data(),
             static_cast<int>(right.size()), TRUE) == CSTR_EQUAL;
}

}  // namespace

ContextCaptureClient::ContextCaptureClient() noexcept {
  event_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  overlapped_.hEvent = event_;
}

ContextCaptureClient::~ContextCaptureClient() {
  Close();
  if (event_ != nullptr) {
    CloseHandle(event_);
    event_ = nullptr;
  }
}

ContextPushResult ContextCaptureClient::TryPush(
    context::ContextFrame frame) noexcept {
  std::vector<std::uint8_t> bytes;
  if (!context::EncodeContextFrame(frame, &bytes)) {
    context::TraceContextPipeline(
        L"tsf-client", L"event=push result=encode-failure");
    return ContextPushResult::kDropped;
  }

  try {
    std::lock_guard lock(mutex_);
    ReapCompletedLocked();
    if (write_in_flight_) {
      // Latest wins. A clear frame naturally replaces an older pending normal
      // frame without waiting for the in-flight write to finish.
      pending_ = std::move(bytes);
      context::TraceContextPipeline(
          L"tsf-client", L"event=push result=coalesced");
      return ContextPushResult::kCoalesced;
    }
    if (pipe_ == INVALID_HANDLE_VALUE && !ConnectVerified()) {
      pending_.reset();
      context::TraceContextPipeline(
          L"tsf-client", L"event=push result=unverified error=%lu",
          GetLastError());
      return ContextPushResult::kUnverified;
    }
    const ContextPushResult result = StartWriteLocked(std::move(bytes));
    context::TraceContextPipeline(
        L"tsf-client", L"event=push result=%d", static_cast<int>(result));
    return result;
  } catch (...) {
    return ContextPushResult::kDropped;
  }
}

void ContextCaptureClient::Close() noexcept {
  try {
    std::lock_guard lock(mutex_);
    CloseLocked();
  } catch (...) {
  }
}

bool ContextCaptureClient::ConnectVerified() noexcept {
  const std::wstring pipe_name = ContextPipeName();
  if (pipe_name.empty()) {
    return false;
  }

  HANDLE pipe = CreateFileW(
      pipe_name.c_str(), GENERIC_WRITE, 0, nullptr, OPEN_EXISTING,
      FILE_FLAG_OVERLAPPED, nullptr);
  if (pipe == INVALID_HANDLE_VALUE) {
    context::TraceContextPipeline(
        L"tsf-client", L"event=connect result=open-failure error=%lu",
        GetLastError());
    return false;
  }
  if (!VerifyServerIdentity(pipe)) {
    context::TraceContextPipeline(
        L"tsf-client", L"event=connect result=identity-failure error=%lu",
        GetLastError());
    CloseHandle(pipe);
    return false;
  }
  pipe_ = pipe;
  context::TraceContextPipeline(
      L"tsf-client", L"event=connect result=verified");
  return true;
}

bool ContextCaptureClient::VerifyServerIdentity(HANDLE pipe) noexcept {
  ULONG server_pid = 0;
  if (!GetNamedPipeServerProcessId(pipe, &server_pid) || server_pid == 0) {
    return false;
  }
  HANDLE process = OpenProcess(
      PROCESS_QUERY_LIMITED_INFORMATION, FALSE, static_cast<DWORD>(server_pid));
  if (process == nullptr) {
    return false;
  }

  std::wstring server_path(32768, L'\0');
  DWORD size = static_cast<DWORD>(server_path.size());
  const bool queried = QueryFullProcessImageNameW(
      process, 0, server_path.data(), &size) != FALSE;
  CloseHandle(process);
  if (!queried || size == 0 || size >= server_path.size()) {
    return false;
  }
  server_path.resize(size);

  const std::wstring directory = ModuleDirectory();
  if (directory.empty()) {
    return false;
  }
  try {
    const std::wstring expected =
        (std::filesystem::path(directory) / kExpectedServerName).wstring();
    return SamePath(server_path, expected);
  } catch (...) {
    return false;
  }
}

void ContextCaptureClient::ReapCompletedLocked() noexcept {
  if (!write_in_flight_ || pipe_ == INVALID_HANDLE_VALUE) {
    return;
  }
  DWORD transferred = 0;
  if (GetOverlappedResult(pipe_, &overlapped_, &transferred, FALSE)) {
    write_in_flight_ = false;
    in_flight_.clear();
    if (pending_.has_value()) {
      auto next = std::move(*pending_);
      pending_.reset();
      StartWriteLocked(std::move(next));
    }
    return;
  }
  if (GetLastError() != ERROR_IO_INCOMPLETE) {
    CloseLocked();
  }
}

ContextPushResult ContextCaptureClient::StartWriteLocked(
    std::vector<std::uint8_t> bytes) noexcept {
  if (pipe_ == INVALID_HANDLE_VALUE || event_ == nullptr || bytes.empty()) {
    return ContextPushResult::kDropped;
  }

  ResetEvent(event_);
  OVERLAPPED fresh{};
  fresh.hEvent = event_;
  overlapped_ = fresh;
  in_flight_ = std::move(bytes);

  if (WriteFile(
          pipe_, in_flight_.data(), static_cast<DWORD>(in_flight_.size()),
          nullptr, &overlapped_)) {
    in_flight_.clear();
    write_in_flight_ = false;
    return ContextPushResult::kSent;
  }
  if (GetLastError() == ERROR_IO_PENDING) {
    write_in_flight_ = true;
    return ContextPushResult::kSent;
  }

  CloseLocked();
  return ContextPushResult::kDropped;
}

void ContextCaptureClient::CloseLocked() noexcept {
  if (pipe_ != INVALID_HANDLE_VALUE) {
    if (write_in_flight_) {
      CancelIoEx(pipe_, &overlapped_);
    }
    CloseHandle(pipe_);
    pipe_ = INVALID_HANDLE_VALUE;
  }
  write_in_flight_ = false;
  in_flight_.clear();
  pending_.reset();
}

}  // namespace neural_weasel::tsf
