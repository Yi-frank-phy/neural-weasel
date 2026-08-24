#pragma once

#include <Windows.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>

#include "pipe/named_pipe_client.h"
#include "tsf/surrounding_text_edit_session.h"

namespace neural_weasel::context {

enum class EditorSecurityLabel {
  kNormal,
  kPrivate,
  kPassword,
};

struct ContextUpdateMetadata {
  std::wstring application_id;
  std::string session_id;
  std::string source_capability;
  std::uint64_t source_revision = 0;
  EditorSecurityLabel security_label = EditorSecurityLabel::kNormal;
  bool secure = true;
  bool partial = true;
};

class ContextUpdateTransport {
 public:
  virtual ~ContextUpdateTransport() = default;
  virtual pipe::QueryResult TryQuery(
      std::string_view utf8_json,
      std::chrono::milliseconds timeout) = 0;
};

class NamedPipeContextUpdateTransport final : public ContextUpdateTransport {
 public:
  explicit NamedPipeContextUpdateTransport(std::wstring pipe_name = {});

  pipe::QueryResult TryQuery(
      std::string_view utf8_json,
      std::chrono::milliseconds timeout) override;

 private:
  pipe::NamedPipeClient client_;
};

enum class ContextUpdateResult {
  kIdle,
  kQueued,
  kPublished,
  kSecureContextCleared,
  kSuperseded,
  kTransportError,
  kProtocolError,
  kReadinessTimeout,
};

struct ContextUpdateBridgeOptions {
  std::chrono::milliseconds pipe_query_timeout{25};
  std::chrono::milliseconds readiness_timeout{3000};
  std::chrono::milliseconds health_poll_interval{5};
};

// Server-side bridge. Submit only queues the newest snapshot; all model-pipe
// request/response I/O and readiness polling run on this owned worker thread.
class ContextUpdateBridge final {
 public:
  ContextUpdateBridge(
      std::unique_ptr<ContextUpdateTransport> transport,
      ContextUpdateBridgeOptions options = {});
  ~ContextUpdateBridge();

  ContextUpdateBridge(const ContextUpdateBridge&) = delete;
  ContextUpdateBridge& operator=(const ContextUpdateBridge&) = delete;

  std::uint64_t Submit(
      tsf::SurroundingTextSnapshot snapshot,
      ContextUpdateMetadata metadata);

  // Invalidates queued/in-flight publication and clears the identity exposed to
  // candidate queries without waiting for the worker's current pipe operation.
  void Invalidate() noexcept;
  void Stop() noexcept;

  std::uint64_t latest_sequence() const noexcept {
    return latest_sequence_.load(std::memory_order_acquire);
  }
  ContextUpdateResult last_result() const noexcept {
    return last_result_.load(std::memory_order_acquire);
  }

 private:
  struct PendingUpdate {
    std::uint64_t sequence = 0;
    tsf::SurroundingTextSnapshot snapshot;
    ContextUpdateMetadata metadata;
  };

  void WorkerLoop() noexcept;
  void Process(PendingUpdate update) noexcept;
  bool IsLatest(std::uint64_t sequence) const noexcept;
  void SetResult(ContextUpdateResult result) noexcept;

  std::unique_ptr<ContextUpdateTransport> transport_;
  ContextUpdateBridgeOptions options_;
  mutable std::mutex mutex_;
  std::condition_variable condition_;
  std::optional<PendingUpdate> cleanup_pending_;
  std::optional<PendingUpdate> pending_;
  std::thread worker_;
  std::uint64_t next_sequence_ = 0;
  bool stopping_ = false;
  std::atomic<std::uint64_t> latest_sequence_{0};
  std::atomic<ContextUpdateResult> last_result_{ContextUpdateResult::kIdle};
};

}  // namespace neural_weasel::context
