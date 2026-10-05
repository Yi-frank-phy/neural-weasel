#include "context/context_update_bridge.h"

#include <algorithm>
#include <charconv>
#include <limits>
#include <system_error>
#include <utility>

#include "context/metadata_trace.h"
#include "rime/editor_context_epoch.h"

namespace neural_weasel::context {
namespace {

using Clock = std::chrono::steady_clock;

bool Utf16ToUtf8(std::wstring_view source, std::string* destination) {
  destination->clear();
  if (source.empty()) {
    return true;
  }
  if (source.size() >
      static_cast<std::size_t>((std::numeric_limits<int>::max)())) {
    return false;
  }
  const int source_size = static_cast<int>(source.size());
  const int required =
      WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, source.data(),
                          source_size, nullptr, 0, nullptr, nullptr);
  if (required <= 0) {
    return false;
  }
  destination->resize(static_cast<std::size_t>(required));
  return WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, source.data(),
                             source_size, destination->data(), required,
                             nullptr, nullptr) == required;
}

void AppendJsonString(std::string_view value, std::string* output) {
  static constexpr char kHex[] = "0123456789abcdef";
  output->push_back('"');
  for (const unsigned char character : value) {
    switch (character) {
      case '"':
        output->append("\\\"");
        break;
      case '\\':
        output->append("\\\\");
        break;
      case '\b':
        output->append("\\b");
        break;
      case '\f':
        output->append("\\f");
        break;
      case '\n':
        output->append("\\n");
        break;
      case '\r':
        output->append("\\r");
        break;
      case '\t':
        output->append("\\t");
        break;
      default:
        if (character < 0x20U) {
          output->append("\\u00");
          output->push_back(kHex[(character >> 4U) & 0x0fU]);
          output->push_back(kHex[character & 0x0fU]);
        } else {
          output->push_back(static_cast<char>(character));
        }
    }
  }
  output->push_back('"');
}

void AppendUnsigned(std::uint64_t value, std::string* output) {
  char buffer[32]{};
  const auto result = std::to_chars(buffer, buffer + sizeof(buffer), value);
  output->append(buffer, result.ptr);
}

void AppendSigned(std::int64_t value, std::string* output) {
  char buffer[32]{};
  const auto result = std::to_chars(buffer, buffer + sizeof(buffer), value);
  output->append(buffer, result.ptr);
}

std::string RequestId(std::uint64_t sequence) {
  std::string value = "ctx-";
  AppendUnsigned(sequence, &value);
  return value;
}

std::string SessionId(const ContextUpdateMetadata& metadata) {
  if (metadata.session_id.empty() || metadata.session_id.size() > 128U) {
    return "context-bridge";
  }
  for (const unsigned char character : metadata.session_id) {
    const bool allowed =
        (character >= 'a' && character <= 'z') ||
        (character >= 'A' && character <= 'Z') ||
        (character >= '0' && character <= '9') || character == '-' ||
        character == '_' || character == '.' || character == ':';
    if (!allowed) {
      return "context-bridge";
    }
  }
  return metadata.session_id;
}

bool IsLowerHexCapability(std::string_view capability) {
  if (capability.size() != 32U) {
    return false;
  }
  return std::all_of(capability.begin(), capability.end(), [](char value) {
    return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f');
  });
}

bool HasSourceIdentity(const ContextUpdateMetadata& metadata) {
  return IsLowerHexCapability(metadata.source_capability) &&
         metadata.source_revision > 0 &&
         metadata.security_label != EditorSecurityLabel::kPassword;
}

const char* SecurityLabelName(EditorSecurityLabel label) {
  switch (label) {
    case EditorSecurityLabel::kPrivate:
      return "private";
    case EditorSecurityLabel::kNormal:
      return "normal";
    case EditorSecurityLabel::kPassword:
      return "password";
  }
  return "normal";
}

bool RequiresImmediateCleanup(
    const tsf::SurroundingTextSnapshot& snapshot,
    const ContextUpdateMetadata& metadata) {
  return metadata.secure || metadata.security_label == EditorSecurityLabel::kPassword ||
         snapshot.result != S_OK;
}

struct SerializedRequest {
  std::string payload;
  bool context_update = false;
};

SerializedRequest BuildSecureFocusJson(
    std::uint64_t sequence,
    const ContextUpdateMetadata& metadata) {
  std::string payload = "{\"type\":\"focus\",\"request_id\":";
  AppendJsonString(RequestId(sequence), &payload);
  payload.append(",\"session_id\":");
  AppendJsonString(SessionId(metadata), &payload);
  payload.append(",\"focused\":true,\"secure\":true}");
  return {std::move(payload), false};
}

SerializedRequest BuildContextRequest(
    std::uint64_t sequence,
    const tsf::SurroundingTextSnapshot& snapshot,
    const ContextUpdateMetadata& metadata) {
  if (RequiresImmediateCleanup(snapshot, metadata)) {
    return BuildSecureFocusJson(sequence, metadata);
  }

  std::string application_id;
  std::string before;
  std::string after;
  if (!Utf16ToUtf8(metadata.application_id, &application_id) ||
      !Utf16ToUtf8(snapshot.before, &before) ||
      !Utf16ToUtf8(snapshot.after, &after)) {
    return BuildSecureFocusJson(sequence, metadata);
  }

  const bool partial = metadata.partial || snapshot.partial;
  const bool complete_region =
      snapshot.before_reached_region_boundary &&
      snapshot.after_reached_region_boundary;

  std::string payload;
  payload.reserve(320U + before.size() + after.size());
  payload.append("{\"type\":\"context_update\",\"request_id\":");
  AppendJsonString(RequestId(sequence), &payload);
  payload.append(",\"context_epoch\":");
  AppendUnsigned(sequence, &payload);
  payload.append(",\"revision\":");
  AppendUnsigned(sequence, &payload);
  payload.append(",\"sequence\":");
  AppendUnsigned(sequence, &payload);
  payload.append(",\"session_id\":");
  AppendJsonString(SessionId(metadata), &payload);
  payload.append(",\"app_id\":");
  AppendJsonString(application_id, &payload);
  payload.append(",\"secure\":false,\"capture_allowed\":true");
  payload.append(",\"partial\":");
  payload.append(partial ? "true" : "false");
  payload.append(",\"complete_region\":");
  payload.append(complete_region ? "true" : "false");
  payload.append(",\"capture_hresult\":");
  AppendSigned(static_cast<std::int32_t>(snapshot.result), &payload);
  if (HasSourceIdentity(metadata)) {
    payload.append(",\"context_session\":");
    AppendJsonString(metadata.source_capability, &payload);
    payload.append(",\"source_revision\":");
    AppendUnsigned(metadata.source_revision, &payload);
    payload.append(",\"security_label\":");
    AppendJsonString(SecurityLabelName(metadata.security_label), &payload);
  }
  payload.append(",\"before\":");
  AppendJsonString(before, &payload);
  payload.append(",\"after\":");
  AppendJsonString(after, &payload);
  payload.push_back('}');
  return {std::move(payload), true};
}

const char* FindCanonicalField(std::string_view json, std::string_view key) {
  std::string needle;
  needle.reserve(key.size() + 3U);
  needle.push_back('"');
  needle.append(key);
  needle.append("\":");
  const std::size_t position = json.find(needle);
  if (position == std::string_view::npos ||
      json.find(needle, position + needle.size()) != std::string_view::npos) {
    return nullptr;
  }
  return json.data() + position + needle.size();
}

bool HasStringField(std::string_view json,
                    std::string_view key,
                    std::string_view expected) {
  const char* value = FindCanonicalField(json, key);
  if (value == nullptr || value >= json.data() + json.size() || *value != '"') {
    return false;
  }
  const std::size_t remaining =
      static_cast<std::size_t>(json.data() + json.size() - value);
  if (remaining < expected.size() + 2U) {
    return false;
  }
  return std::string_view(value + 1, expected.size()) == expected &&
         value[expected.size() + 1U] == '"';
}

bool HasBooleanField(std::string_view json,
                     std::string_view key,
                     bool expected) {
  const char* value = FindCanonicalField(json, key);
  if (value == nullptr) {
    return false;
  }
  const std::string_view literal = expected ? "true" : "false";
  const std::size_t remaining =
      static_cast<std::size_t>(json.data() + json.size() - value);
  return remaining >= literal.size() &&
         std::string_view(value, literal.size()) == literal;
}

bool ReadUnsignedField(std::string_view json,
                       std::string_view key,
                       std::uint64_t* result) {
  const char* value = FindCanonicalField(json, key);
  if (value == nullptr) {
    return false;
  }
  const char* end = json.data() + json.size();
  const auto parsed = std::from_chars(value, end, *result);
  return parsed.ec == std::errc{} && parsed.ptr != value &&
         (parsed.ptr == end || *parsed.ptr == ',' || *parsed.ptr == '}');
}

bool ParseContextUpdateAcknowledgement(std::string_view json,
                                       std::uint64_t sequence,
                                       std::uint64_t* assigned_epoch,
                                       std::string_view type = "context_update") {
  std::uint64_t client_epoch = 0;
  return HasStringField(json, "type", type) &&
         HasBooleanField(json, "ok", true) &&
         HasBooleanField(json, "accepted", true) &&
         HasStringField(json, "request_id", RequestId(sequence)) &&
         ReadUnsignedField(json, "client_context_epoch", &client_epoch) &&
         client_epoch == sequence &&
         ReadUnsignedField(json, "context_epoch", assigned_epoch) &&
         *assigned_epoch > 0;
}

bool ParseSecureFocusAcknowledgement(std::string_view json,
                                     std::uint64_t sequence,
                                     std::string_view session_id) {
  return HasStringField(json, "type", "focus") &&
         HasBooleanField(json, "ok", true) &&
         HasBooleanField(json, "accepted", true) &&
         HasStringField(json, "request_id", RequestId(sequence)) &&
         HasStringField(json, "session_id", session_id) &&
         HasBooleanField(json, "secure", true);
}

bool ParseReadyEpoch(std::string_view json,
                     std::uint64_t sequence,
                     std::uint64_t* ready_epoch) {
  return HasStringField(json, "type", "health") &&
         HasBooleanField(json, "ok", true) &&
         HasStringField(json, "request_id", RequestId(sequence)) &&
         ReadUnsignedField(json, "context_epoch", ready_epoch);
}

std::string BuildHealthJson(std::uint64_t sequence) {
  std::string payload = "{\"type\":\"health\",\"request_id\":";
  AppendJsonString(RequestId(sequence), &payload);
  payload.push_back('}');
  return payload;
}

std::string BuildReceiptJson(std::uint64_t sequence,
                             const ContextUpdateMetadata& metadata) {
  std::string payload = "{\"type\":\"context_update_receipt\",\"request_id\":";
  AppendJsonString(RequestId(sequence), &payload);
  payload.append(",\"session_id\":");
  AppendJsonString(SessionId(metadata), &payload);
  payload.append(",\"context_epoch\":");
  AppendUnsigned(sequence, &payload);
  if (HasSourceIdentity(metadata)) {
    payload.append(",\"context_session\":");
    AppendJsonString(metadata.source_capability, &payload);
    payload.append(",\"source_revision\":");
    AppendUnsigned(metadata.source_revision, &payload);
    payload.append(",\"security_label\":");
    AppendJsonString(SecurityLabelName(metadata.security_label), &payload);
  }
  payload.push_back('}');
  return payload;
}

bool IsRecoverable(const pipe::QueryResult& result) {
  // Identity failures and invalid protocol/payloads must fail closed.
  return result.win32_error != ERROR_ACCESS_DENIED &&
         result.win32_error != ERROR_NO_TOKEN &&
         (result.status == pipe::QueryStatus::kDisconnected ||
          result.status == pipe::QueryStatus::kTimeout ||
          result.status == pipe::QueryStatus::kBusy);
}

std::chrono::milliseconds RemainingTimeout(
    Clock::time_point deadline,
    std::chrono::milliseconds maximum) {
  const auto now = Clock::now();
  if (now >= deadline) {
    return std::chrono::milliseconds{0};
  }
  const auto remaining =
      std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now);
  return (std::min)(remaining, maximum);
}

}  // namespace

NamedPipeContextUpdateTransport::NamedPipeContextUpdateTransport(
    std::wstring pipe_name)
    : client_(std::move(pipe_name)) {}

pipe::QueryResult NamedPipeContextUpdateTransport::TryQuery(
    std::string_view utf8_json,
    std::chrono::milliseconds timeout) {
  return client_.TryQuery(utf8_json, timeout);
}

ContextUpdateBridge::ContextUpdateBridge(
    std::unique_ptr<ContextUpdateTransport> transport,
    ContextUpdateBridgeOptions options)
    : transport_(std::move(transport)), options_(options) {
  worker_ = std::thread(&ContextUpdateBridge::WorkerLoop, this);
}

ContextUpdateBridge::~ContextUpdateBridge() {
  Stop();
}

std::uint64_t ContextUpdateBridge::Submit(
    tsf::SurroundingTextSnapshot snapshot,
    ContextUpdateMetadata metadata) {
  std::lock_guard lock(mutex_);
  if (stopping_ ||
      next_sequence_ == (std::numeric_limits<std::uint64_t>::max)()) {
    return 0;
  }
  const std::uint64_t sequence = ++next_sequence_;
  latest_sequence_.store(sequence, std::memory_order_release);
  PendingUpdate update{sequence, std::move(snapshot), std::move(metadata)};
  const bool immediate_cleanup =
      RequiresImmediateCleanup(update.snapshot, update.metadata);
  const std::uint64_t source_revision = update.metadata.source_revision;
  if (immediate_cleanup) {
    rime_plugin::EditorContextEpoch::Instance().Reset();
    cleanup_pending_ = std::move(update);
    pending_.reset();
  } else {
    pending_ = std::move(update);
  }
  SetResult(ContextUpdateResult::kQueued);
  TraceContextPipeline(
      L"bridge", L"event=submit sequence=%llu cleanup=%d revision=%llu",
      static_cast<unsigned long long>(sequence), immediate_cleanup ? 1 : 0,
      static_cast<unsigned long long>(source_revision));
  condition_.notify_one();
  return sequence;
}

void ContextUpdateBridge::Invalidate() noexcept {
  std::lock_guard lock(mutex_);
  if (next_sequence_ != (std::numeric_limits<std::uint64_t>::max)()) {
    latest_sequence_.store(++next_sequence_, std::memory_order_release);
  }
  cleanup_pending_.reset();
  pending_.reset();
  rime_plugin::EditorContextEpoch::Instance().Reset();
  SetResult(ContextUpdateResult::kSuperseded);
}

void ContextUpdateBridge::Stop() noexcept {
  {
    std::lock_guard lock(mutex_);
    if (stopping_) {
      return;
    }
    stopping_ = true;
    if (next_sequence_ != (std::numeric_limits<std::uint64_t>::max)()) {
      latest_sequence_.store(++next_sequence_, std::memory_order_release);
    }
    cleanup_pending_.reset();
    pending_.reset();
    rime_plugin::EditorContextEpoch::Instance().Reset();
  }
  condition_.notify_all();
  if (worker_.joinable()) {
    worker_.join();
  }
}

void ContextUpdateBridge::WorkerLoop() noexcept {
  while (true) {
    PendingUpdate update;
    {
      std::unique_lock lock(mutex_);
      condition_.wait(lock, [this] {
        return stopping_ || cleanup_pending_.has_value() || pending_.has_value();
      });
      if (stopping_) {
        return;
      }
      if (cleanup_pending_.has_value()) {
        update = std::move(*cleanup_pending_);
        cleanup_pending_.reset();
      } else {
        update = std::move(*pending_);
        pending_.reset();
      }
    }
    Process(std::move(update));
  }
}

void ContextUpdateBridge::Process(PendingUpdate update) noexcept {
  TraceContextPipeline(
      L"bridge", L"event=process begin sequence=%llu",
      static_cast<unsigned long long>(update.sequence));
  const SerializedRequest request =
      BuildContextRequest(update.sequence, update.snapshot, update.metadata);
  if (!request.context_update) {
    std::lock_guard lock(mutex_);
    rime_plugin::EditorContextEpoch::Instance().Reset();
  }
  if (transport_ == nullptr) {
    SetResult(ContextUpdateResult::kTransportError);
    TraceContextPipeline(
        L"bridge", L"event=process result=transport-null sequence=%llu",
        static_cast<unsigned long long>(update.sequence));
    return;
  }
  if (request.context_update && !IsLatest(update.sequence)) {
    SetResult(ContextUpdateResult::kSuperseded);
    return;
  }

  const auto deadline = Clock::now() + options_.readiness_timeout;
  auto timeout = RemainingTimeout(deadline, options_.pipe_query_timeout);
  if (timeout.count() <= 0) {
    SetResult(ContextUpdateResult::kReadinessTimeout);
    return;
  }
  // Shared deadline and retry allowance for update, receipt and health. This
  // never changes TSF's synchronous budget; only this server-owned worker waits.
  unsigned recovery_attempts = 2;
  const auto wait_for_recovery = [&]() {
    if (recovery_attempts == 0) return false;
    --recovery_attempts;
    std::unique_lock lock(mutex_);
    condition_.wait_until(lock, (std::min)(deadline, Clock::now() +
        std::chrono::milliseconds(25)), [&] {
      return stopping_ || (request.context_update && !IsLatest(update.sequence));
    });
    return !stopping_ && (!request.context_update || IsLatest(update.sequence)) &&
           Clock::now() < deadline;
  };
  pipe::QueryResult response = transport_->TryQuery(request.payload, timeout);
  bool recovered_ack = false;
  while (!response && IsRecoverable(response) && wait_for_recovery()) {
    timeout = RemainingTimeout(deadline, options_.pipe_query_timeout);
    if (timeout.count() <= 0) break;
    if (request.context_update) {
      // A failed read can mean the update was already accepted. Query only its
      // metadata receipt before deciding whether to resend the original bytes.
      response = transport_->TryQuery(
          BuildReceiptJson(update.sequence, update.metadata), timeout);
      if (!IsLatest(update.sequence)) break;
      if (!response) continue;
      std::uint64_t receipt_epoch = 0;
      if (ParseContextUpdateAcknowledgement(response.payload, update.sequence,
              &receipt_epoch, "context_update_receipt")) {
        recovered_ack = true;
        break;
      }
      std::uint64_t client_epoch = 0;
      std::uint64_t receipt_epoch_missing = 1;
      if (!HasStringField(response.payload, "type", "context_update_receipt") ||
          !HasBooleanField(response.payload, "ok", true) ||
          !HasBooleanField(response.payload, "accepted", false) ||
          !HasStringField(response.payload, "request_id", RequestId(update.sequence)) ||
          !ReadUnsignedField(response.payload, "client_context_epoch", &client_epoch) ||
          client_epoch != update.sequence ||
          !ReadUnsignedField(response.payload, "context_epoch", &receipt_epoch_missing) ||
          receipt_epoch_missing != 0) {
        SetResult(ContextUpdateResult::kProtocolError);
        return;
      }
      timeout = RemainingTimeout(deadline, options_.pipe_query_timeout);
      if (timeout.count() <= 0) break;
    }
    response = transport_->TryQuery(request.payload, timeout);
  }
  if (request.context_update && !IsLatest(update.sequence)) {
    SetResult(ContextUpdateResult::kSuperseded);
    return;
  }
  if (!response) {
    SetResult(ContextUpdateResult::kTransportError);
    TraceContextPipeline(
        L"bridge",
        L"event=process result=update-transport-error sequence=%llu status=%d error=%lu",
        static_cast<unsigned long long>(update.sequence),
        static_cast<int>(response.status), response.win32_error);
    return;
  }

  if (!request.context_update) {
    if (!ParseSecureFocusAcknowledgement(
            response.payload, update.sequence, SessionId(update.metadata))) {
      SetResult(ContextUpdateResult::kProtocolError);
      return;
    }
    SetResult(ContextUpdateResult::kSecureContextCleared);
    return;
  }

  std::uint64_t assigned_epoch = 0;
  if (!ParseContextUpdateAcknowledgement(
          response.payload, update.sequence, &assigned_epoch,
          recovered_ack ? "context_update_receipt" : "context_update")) {
    SetResult(ContextUpdateResult::kProtocolError);
    TraceContextPipeline(
        L"bridge", L"event=process result=update-protocol-error sequence=%llu",
        static_cast<unsigned long long>(update.sequence));
    return;
  }
  if (!IsLatest(update.sequence)) {
    SetResult(ContextUpdateResult::kSuperseded);
    return;
  }

  const std::string health_request = BuildHealthJson(update.sequence);
  while (IsLatest(update.sequence)) {
    timeout = RemainingTimeout(deadline, options_.pipe_query_timeout);
    if (timeout.count() <= 0) {
      SetResult(ContextUpdateResult::kReadinessTimeout);
      return;
    }
    const pipe::QueryResult health =
        transport_->TryQuery(health_request, timeout);
    if (!health) {
      if (IsRecoverable(health) && wait_for_recovery()) continue;
      SetResult(ContextUpdateResult::kTransportError);
      TraceContextPipeline(
          L"bridge",
          L"event=process result=health-transport-error sequence=%llu status=%d error=%lu",
          static_cast<unsigned long long>(update.sequence),
          static_cast<int>(health.status), health.win32_error);
      return;
    }

    // Epoch counters are scoped to one service lifetime. A restarted process
    // can report the same numeric epoch for unrelated editor context.
    if (health.server_identity != response.server_identity) {
      SetResult(ContextUpdateResult::kSuperseded);
      return;
    }
    std::uint64_t ready_epoch = 0;
    if (!ParseReadyEpoch(health.payload, update.sequence, &ready_epoch)) {
      SetResult(ContextUpdateResult::kProtocolError);
      return;
    }
    if (ready_epoch == assigned_epoch) {
      std::lock_guard lock(mutex_);
      if (stopping_ ||
          latest_sequence_.load(std::memory_order_acquire) != update.sequence) {
        SetResult(ContextUpdateResult::kSuperseded);
        return;
      }
      if (HasSourceIdentity(update.metadata)) {
        rime_plugin::EditorContextEpoch::Instance().Publish(
            rime_plugin::AcceptedEditorContext{
                assigned_epoch,
                update.metadata.source_capability,
                update.metadata.source_revision,
            });
      } else {
        rime_plugin::EditorContextEpoch::Instance().Publish(assigned_epoch);
      }
      SetResult(ContextUpdateResult::kPublished);
      TraceContextPipeline(
          L"bridge", L"event=process result=published sequence=%llu epoch=%llu revision=%llu",
          static_cast<unsigned long long>(update.sequence),
          static_cast<unsigned long long>(assigned_epoch),
          static_cast<unsigned long long>(update.metadata.source_revision));
      return;
    }
    if (ready_epoch > assigned_epoch) {
      SetResult(ContextUpdateResult::kSuperseded);
      return;
    }

    const auto remaining =
        RemainingTimeout(deadline, options_.health_poll_interval);
    if (remaining.count() <= 0) {
      SetResult(ContextUpdateResult::kReadinessTimeout);
      return;
    }
    std::this_thread::sleep_for(remaining);
  }
  SetResult(ContextUpdateResult::kSuperseded);
}

bool ContextUpdateBridge::IsLatest(std::uint64_t sequence) const noexcept {
  return latest_sequence_.load(std::memory_order_acquire) == sequence;
}

void ContextUpdateBridge::SetResult(ContextUpdateResult result) noexcept {
  last_result_.store(result, std::memory_order_release);
}

}  // namespace neural_weasel::context
