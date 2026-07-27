#include "pipe/named_pipe_client.h"

#include <sddl.h>

#include <algorithm>
#include <array>
#include <limits>
#include <utility>
#include <vector>

namespace neural_weasel::pipe {
namespace {

bool ReadTokenUserSid(HANDLE token, std::vector<std::uint8_t>* storage) {
  DWORD required = 0;
  GetTokenInformation(token, TokenUser, nullptr, 0, &required);
  if (required == 0 || GetLastError() != ERROR_INSUFFICIENT_BUFFER) {
    return false;
  }
  storage->resize(required);
  return GetTokenInformation(token, TokenUser, storage->data(), required,
                             &required) != FALSE;
}

DWORD RemainingMilliseconds(std::chrono::steady_clock::time_point deadline) {
  const auto now = std::chrono::steady_clock::now();
  if (now >= deadline) {
    return 0;
  }
  const auto remaining =
      std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now);
  return static_cast<DWORD>((std::max)(std::int64_t{1}, remaining.count()));
}

std::array<std::uint8_t, 4> EncodeLength(std::uint32_t value) {
  return {
      static_cast<std::uint8_t>(value & 0xffU),
      static_cast<std::uint8_t>((value >> 8U) & 0xffU),
      static_cast<std::uint8_t>((value >> 16U) & 0xffU),
      static_cast<std::uint8_t>((value >> 24U) & 0xffU),
  };
}

std::uint32_t DecodeLength(const std::array<std::uint8_t, 4>& bytes) {
  return static_cast<std::uint32_t>(bytes[0]) |
         (static_cast<std::uint32_t>(bytes[1]) << 8U) |
         (static_cast<std::uint32_t>(bytes[2]) << 16U) |
         (static_cast<std::uint32_t>(bytes[3]) << 24U);
}

}  // namespace

std::wstring CurrentUserPipeName() {
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
    return {};
  }

  std::vector<std::uint8_t> storage;
  const bool read = ReadTokenUserSid(token, &storage);
  CloseHandle(token);
  if (!read) {
    return {};
  }

  const auto* token_user =
      reinterpret_cast<const TOKEN_USER*>(storage.data());
  LPWSTR sid_text = nullptr;
  if (!ConvertSidToStringSidW(token_user->User.Sid, &sid_text)) {
    return {};
  }

  std::wstring pipe_name = LR"(\\.\pipe\NeuralWeasel-v1-)";
  pipe_name += sid_text;
  LocalFree(sid_text);
  return pipe_name;
}

NamedPipeClient::NamedPipeClient(std::wstring pipe_name,
                                 std::uint32_t max_payload_bytes)
    : pipe_name_(pipe_name.empty() ? CurrentUserPipeName()
                                   : std::move(pipe_name)),
      max_payload_bytes_(max_payload_bytes) {}

NamedPipeClient::~NamedPipeClient() {
  Disconnect();
}

QueryResult NamedPipeClient::TryQuery(std::string_view utf8_json,
                                      std::chrono::milliseconds timeout) {
  if (utf8_json.size() > max_payload_bytes_ ||
      utf8_json.size() > (std::numeric_limits<std::uint32_t>::max)()) {
    return {QueryStatus::kPayloadTooLarge, {}, ERROR_BUFFER_OVERFLOW};
  }

  std::unique_lock lock(mutex_, std::try_to_lock);
  if (!lock.owns_lock()) {
    return {QueryStatus::kBusy, {}, ERROR_BUSY};
  }

  const auto deadline = Clock::now() + timeout;
  DWORD error = ERROR_SUCCESS;
  if (!ConnectUntil(deadline, &error)) {
    return {error == ERROR_SEM_TIMEOUT ? QueryStatus::kTimeout
                                      : QueryStatus::kDisconnected,
            {}, error};
  }

  const auto request_size = static_cast<std::uint32_t>(utf8_json.size());
  auto request_header = EncodeLength(request_size);
  if (!Transfer(true, request_header.data(), request_header.size(), deadline,
                &error) ||
      !Transfer(true, const_cast<char*>(utf8_json.data()), utf8_json.size(),
                deadline, &error)) {
    DisconnectLocked();
    return {error == ERROR_SEM_TIMEOUT ? QueryStatus::kTimeout
                                      : QueryStatus::kDisconnected,
            {}, error};
  }

  std::array<std::uint8_t, 4> response_header{};
  if (!Transfer(false, response_header.data(), response_header.size(), deadline,
                &error)) {
    DisconnectLocked();
    return {error == ERROR_SEM_TIMEOUT ? QueryStatus::kTimeout
                                      : QueryStatus::kDisconnected,
            {}, error};
  }

  const auto response_size = DecodeLength(response_header);
  if (response_size > max_payload_bytes_) {
    DisconnectLocked();
    return {QueryStatus::kProtocolError, {}, ERROR_INVALID_DATA};
  }

  std::string response(response_size, '\0');
  if (!Transfer(false, response.data(), response.size(), deadline, &error)) {
    DisconnectLocked();
    return {error == ERROR_SEM_TIMEOUT ? QueryStatus::kTimeout
                                      : QueryStatus::kDisconnected,
            {}, error};
  }
  return {QueryStatus::kOk, std::move(response), ERROR_SUCCESS};
}

void NamedPipeClient::Disconnect() {
  std::lock_guard lock(mutex_);
  DisconnectLocked();
}

bool NamedPipeClient::connected() const noexcept {
  std::lock_guard lock(mutex_);
  return pipe_ != INVALID_HANDLE_VALUE;
}

bool NamedPipeClient::ConnectUntil(Clock::time_point deadline, DWORD* error) {
  if (pipe_ != INVALID_HANDLE_VALUE) {
    return true;
  }
  if (pipe_name_.empty()) {
    *error = ERROR_NO_TOKEN;
    return false;
  }

  const DWORD wait_ms = RemainingMilliseconds(deadline);
  if (wait_ms == 0 || !WaitNamedPipeW(pipe_name_.c_str(), wait_ms)) {
    *error = wait_ms == 0 ? ERROR_SEM_TIMEOUT : GetLastError();
    return false;
  }

  pipe_ = CreateFileW(pipe_name_.c_str(), GENERIC_READ | GENERIC_WRITE, 0,
                      nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr);
  if (pipe_ == INVALID_HANDLE_VALUE) {
    *error = GetLastError();
    return false;
  }

  if (!VerifyServerIdentity(error)) {
    DisconnectLocked();
    return false;
  }

  DWORD mode = PIPE_READMODE_BYTE;
  if (!SetNamedPipeHandleState(pipe_, &mode, nullptr, nullptr)) {
    *error = GetLastError();
    DisconnectLocked();
    return false;
  }
  return true;
}

bool NamedPipeClient::VerifyServerIdentity(DWORD* error) {
  ULONG server_process_id = 0;
  if (!GetNamedPipeServerProcessId(pipe_, &server_process_id)) {
    *error = GetLastError();
    return false;
  }
  if (server_process_id == 0) {
    *error = ERROR_INVALID_DATA;
    return false;
  }

  HANDLE server_process =
      OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, server_process_id);
  if (server_process == nullptr) {
    *error = GetLastError();
    return false;
  }

  HANDLE server_token = nullptr;
  if (!OpenProcessToken(server_process, TOKEN_QUERY, &server_token)) {
    *error = GetLastError();
    CloseHandle(server_process);
    return false;
  }

  HANDLE client_token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &client_token)) {
    *error = GetLastError();
    CloseHandle(server_token);
    CloseHandle(server_process);
    return false;
  }

  std::vector<std::uint8_t> server_user;
  std::vector<std::uint8_t> client_user;
  const bool read_server = ReadTokenUserSid(server_token, &server_user);
  const bool read_client = ReadTokenUserSid(client_token, &client_user);
  CloseHandle(client_token);
  CloseHandle(server_token);
  CloseHandle(server_process);

  if (!read_server || !read_client) {
    *error = ERROR_ACCESS_DENIED;
    return false;
  }

  const auto* server_token_user =
      reinterpret_cast<const TOKEN_USER*>(server_user.data());
  const auto* client_token_user =
      reinterpret_cast<const TOKEN_USER*>(client_user.data());
  if (!IsValidSid(server_token_user->User.Sid) ||
      !IsValidSid(client_token_user->User.Sid) ||
      !EqualSid(server_token_user->User.Sid, client_token_user->User.Sid)) {
    *error = ERROR_ACCESS_DENIED;
    return false;
  }
  return true;
}

bool NamedPipeClient::Transfer(bool write,
                               void* buffer,
                               std::size_t size,
                               Clock::time_point deadline,
                               DWORD* error) {
  auto* cursor = static_cast<std::uint8_t*>(buffer);
  std::size_t remaining = size;

  while (remaining > 0) {
    const DWORD chunk = static_cast<DWORD>(
        (std::min)(remaining,
                   static_cast<std::size_t>(
                       (std::numeric_limits<DWORD>::max)())));

    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (event == nullptr) {
      *error = GetLastError();
      return false;
    }
    OVERLAPPED overlapped{};
    overlapped.hEvent = event;

    DWORD transferred = 0;
    const BOOL started =
        write ? WriteFile(pipe_, cursor, chunk, &transferred, &overlapped)
              : ReadFile(pipe_, cursor, chunk, &transferred, &overlapped);
    if (!started && GetLastError() != ERROR_IO_PENDING) {
      *error = GetLastError();
      CloseHandle(event);
      return false;
    }

    if (!started) {
      const DWORD wait_ms = RemainingMilliseconds(deadline);
      const DWORD wait_result =
          wait_ms == 0 ? WAIT_TIMEOUT
                       : WaitForSingleObject(overlapped.hEvent, wait_ms);
      if (wait_result != WAIT_OBJECT_0) {
        CancelIoEx(pipe_, &overlapped);
        WaitForSingleObject(overlapped.hEvent, INFINITE);
        *error =
            wait_result == WAIT_TIMEOUT ? ERROR_SEM_TIMEOUT : GetLastError();
        CloseHandle(event);
        return false;
      }
      if (!GetOverlappedResult(pipe_, &overlapped, &transferred, FALSE)) {
        *error = GetLastError();
        CloseHandle(event);
        return false;
      }
    }
    CloseHandle(event);

    if (transferred == 0) {
      *error = ERROR_BROKEN_PIPE;
      return false;
    }
    cursor += transferred;
    remaining -= transferred;
  }
  return true;
}

void NamedPipeClient::DisconnectLocked() {
  if (pipe_ != INVALID_HANDLE_VALUE) {
    CancelIoEx(pipe_, nullptr);
    CloseHandle(pipe_);
    pipe_ = INVALID_HANDLE_VALUE;
  }
}

}  // namespace neural_weasel::pipe

