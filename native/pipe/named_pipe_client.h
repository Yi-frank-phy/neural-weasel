#pragma once

#include <Windows.h>

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>

namespace neural_weasel::pipe {

// Matches the Python server's
// \\.\pipe\NeuralWeasel-v1-<current TokenUser SID> convention.
// Returns an empty string when the current process token cannot be queried.
std::wstring CurrentUserPipeName();

enum class QueryStatus {
  kOk,
  kBusy,
  kTimeout,
  kDisconnected,
  kProtocolError,
  kPayloadTooLarge,
};

// OS-derived identity of the process owning this verified pipe connection.
// Creation time prevents a reused PID from identifying an earlier service.
struct ServerProcessIdentity {
  DWORD process_id = 0;
  std::uint64_t creation_time = 0;

  bool operator==(const ServerProcessIdentity& other) const noexcept {
    return process_id == other.process_id && creation_time == other.creation_time;
  }
  bool operator!=(const ServerProcessIdentity& other) const noexcept {
    return !(*this == other);
  }
};

struct QueryResult {
  QueryStatus status = QueryStatus::kDisconnected;
  std::string payload;
  DWORD win32_error = ERROR_SUCCESS;
  ServerProcessIdentity server_identity{};

  explicit operator bool() const noexcept { return status == QueryStatus::kOk; }
};

// A single persistent byte-mode pipe connection carrying:
//   uint32 little-endian payload length + UTF-8 JSON payload.
//
// TryQuery is intentionally fail-fast: it never waits for another caller to
// release the client mutex, and all pipe I/O shares one absolute deadline.
// Before sending bytes, a new connection requires the server process TokenUser
// SID to equal the current process TokenUser SID.
class NamedPipeClient final {
 public:
  explicit NamedPipeClient(
      std::wstring pipe_name = {},
      std::uint32_t max_payload_bytes = 1024U * 1024U);
  ~NamedPipeClient();

  NamedPipeClient(const NamedPipeClient&) = delete;
  NamedPipeClient& operator=(const NamedPipeClient&) = delete;

  QueryResult TryQuery(std::string_view utf8_json,
                       std::chrono::milliseconds timeout);
  void Disconnect();
  bool connected() const noexcept;

 private:
  using Clock = std::chrono::steady_clock;

  bool ConnectUntil(Clock::time_point deadline, DWORD* error);
  bool VerifyServerIdentity(DWORD* error);
  bool Transfer(bool write,
                void* buffer,
                std::size_t size,
                Clock::time_point deadline,
                DWORD* error);
  void DisconnectLocked();

  std::wstring pipe_name_;
  std::uint32_t max_payload_bytes_;
  mutable std::mutex mutex_;
  HANDLE pipe_ = INVALID_HANDLE_VALUE;
  ServerProcessIdentity server_identity_;
};

}  // namespace neural_weasel::pipe
