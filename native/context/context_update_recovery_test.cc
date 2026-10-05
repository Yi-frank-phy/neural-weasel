#include "context/context_update_bridge.h"
#include "rime/editor_context_epoch.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

namespace {
using namespace neural_weasel;
using namespace std::chrono_literals;

pipe::QueryResult Ok(std::string payload) {
  return {pipe::QueryStatus::kOk, std::move(payload), ERROR_SUCCESS};
}

class RecoveryTransport final : public context::ContextUpdateTransport {
 public:
  enum Mode { kBeforeAccept, kLostAck, kHealthDrop, kPermanent,
              kProtocol, kIdentity, kBlocked };
  explicit RecoveryTransport(Mode mode) : mode(mode) {}
  pipe::QueryResult TryQuery(std::string_view request,
                            std::chrono::milliseconds) override {
    ++calls;
    const bool update = request.find("\"type\":\"context_update\"") != request.npos;
    const bool receipt = request.find("\"type\":\"context_update_receipt\"") != request.npos;
    const bool focus = request.find("\"type\":\"focus\"") != request.npos;
    const bool first = request.find("\"request_id\":\"ctx-1\"") != request.npos;
    const unsigned seq = first ? 1 : 2;
    const std::string id = "\"request_id\":\"ctx-" + std::to_string(seq) + "\"";
    if (mode == kBlocked && update && first) {
      std::unique_lock lock(mutex);
      blocked = true;
      condition.notify_all();
      condition.wait(lock, [&] { return released; });
      return {pipe::QueryStatus::kDisconnected, {}, ERROR_NO_DATA};
    }
    if (mode == kPermanent) return {pipe::QueryStatus::kDisconnected, {}, ERROR_NO_DATA};
    if (mode == kIdentity) return {pipe::QueryStatus::kDisconnected, {}, ERROR_ACCESS_DENIED};
    if (mode == kProtocol) return {pipe::QueryStatus::kProtocolError, {}, ERROR_INVALID_DATA};
    if (update) {
      ++updates;
      if (first && updates == 1 && (mode == kBeforeAccept || mode == kLostAck)) {
        accepted = mode == kLostAck;
        return {pipe::QueryStatus::kDisconnected, {}, ERROR_NO_DATA};
      }
      accepted = true;
      return Ok("{\"type\":\"context_update\",\"ok\":true,\"accepted\":true,"
                "\"context_epoch\":7,\"client_context_epoch\":" + std::to_string(seq) + "," + id + "}");
    }
    if (receipt) {
      ++receipts;
      return Ok("{\"type\":\"context_update_receipt\",\"ok\":true,\"accepted\":" +
                std::string(accepted ? "true" : "false") + ",\"context_epoch\":" +
                std::string(accepted ? "7" : "0") + ",\"client_context_epoch\":" +
                std::to_string(seq) + "," + id + "}");
    }
    if (focus) {
      ++cleanups;
      if (request.find("SENSITIVE_SENTINEL") != request.npos) leaked = true;
      return Ok("{\"type\":\"focus\",\"ok\":true,\"accepted\":true,"
                "\"secure\":true,\"session_id\":\"recovery-test\"," + id + "}");
    }
    if (mode == kHealthDrop && health_calls++ == 0)
      return {pipe::QueryStatus::kDisconnected, {}, ERROR_NO_DATA};
    return Ok("{\"type\":\"health\",\"ok\":true,\"context_epoch\":7," + id + "}");
  }
  bool WaitBlocked() {
    std::unique_lock lock(mutex);
    return condition.wait_for(lock, 1s, [&] { return blocked; });
  }
  void Release() {
    std::lock_guard lock(mutex);
    released = true;
    condition.notify_all();
  }
  Mode mode;
  std::atomic<unsigned> calls{0}, updates{0}, receipts{0}, cleanups{0}, health_calls{0};
  bool accepted = false, leaked = false;
  std::mutex mutex;
  std::condition_variable condition;
  bool blocked = false, released = false;
};

class IdentityTransport final : public context::ContextUpdateTransport {
 public:
  explicit IdentityTransport(pipe::ServerProcessIdentity health_identity)
      : health_identity_(health_identity), delegate_(RecoveryTransport::kHealthDrop) {}
  pipe::QueryResult TryQuery(std::string_view request,
                            std::chrono::milliseconds timeout) override {
    auto response = delegate_.TryQuery(request, timeout);
    if (response) {
      response.server_identity = request.find("\"type\":\"health\"") != request.npos
          ? health_identity_ : pipe::ServerProcessIdentity{77, 11};
    }
    return response;
  }
 private:
  pipe::ServerProcessIdentity health_identity_;
  RecoveryTransport delegate_;
};

tsf::SurroundingTextSnapshot Snapshot() {
  tsf::SurroundingTextSnapshot snapshot;
  snapshot.result = S_OK;
  snapshot.before = L"public synthetic fixture";
  return snapshot;
}
context::ContextUpdateMetadata Metadata() {
  context::ContextUpdateMetadata metadata;
  metadata.secure = false;
  metadata.session_id = "recovery-test";
  metadata.source_capability = std::string(32, 'a');
  metadata.source_revision = 1;
  return metadata;
}
bool Wait(context::ContextUpdateBridge& bridge, context::ContextUpdateResult wanted) {
  const auto deadline = std::chrono::steady_clock::now() + 2s;
  while (std::chrono::steady_clock::now() < deadline) {
    if (bridge.last_result() == wanted) return true;
    std::this_thread::sleep_for(1ms);
  }
  return false;
}

// Gate the first real OS query after it completes. The test can deterministically
// supersede it before the worker consumes its response, without fake responses.
class GatedPipeTransport final : public context::ContextUpdateTransport {
 public:
  explicit GatedPipeTransport(std::wstring pipe) : real_(std::move(pipe)) {}
  pipe::QueryResult TryQuery(std::string_view request,
                            std::chrono::milliseconds timeout) override {
    auto response = real_.TryQuery(request, timeout);
    std::unique_lock lock(mutex_);
    if (!blocked_) {
      blocked_ = true;
      condition_.notify_all();
      condition_.wait(lock, [&] { return released_; });
    }
    return response;
  }
  bool WaitBlocked() {
    std::unique_lock lock(mutex_);
    return condition_.wait_for(lock, 2s, [&] { return blocked_; });
  }
  void Release() {
    std::lock_guard lock(mutex_);
    released_ = true;
    condition_.notify_all();
  }
 private:
  context::NamedPipeContextUpdateTransport real_;
  std::mutex mutex_;
  std::condition_variable condition_;
  bool blocked_ = false, released_ = false;
};

int RunRace(std::wstring pipe, std::wstring_view action) {
  auto transport = std::make_unique<GatedPipeTransport>(std::move(pipe));
  auto* gate = transport.get();
  context::ContextUpdateBridge bridge(std::move(transport));
  bridge.Submit(Snapshot(), Metadata());
  if (!gate->WaitBlocked()) { gate->Release(); return 30; }
  auto metadata = Metadata();
  metadata.source_revision = 2;
  auto wanted = context::ContextUpdateResult::kPublished;
  std::uint64_t expected_epoch = 2, expected_revision = 2;
  if (action == L"supersede") {
    bridge.Submit(Snapshot(), metadata);
  } else if (action == L"invalidate") {
    bridge.Invalidate();
    wanted = context::ContextUpdateResult::kSuperseded;
    expected_epoch = expected_revision = 0;
  } else if (action == L"stop") {
    std::thread stopper([&] { bridge.Stop(); });
    gate->Release();
    stopper.join();
    const auto epoch = rime_plugin::EditorContextEpoch::Instance().Load();
    std::cout << "race_stop epoch=" << epoch << '\n';
    return epoch == 0 ? 0 : 31;
  } else {
    metadata.secure = true;
    auto snapshot = Snapshot(); snapshot.before = L"SENSITIVE_SENTINEL";
    bridge.Submit(std::move(snapshot), metadata);
    wanted = context::ContextUpdateResult::kSecureContextCleared;
    expected_epoch = expected_revision = 0;
    if (action == L"barrier") {
      metadata.secure = false; metadata.source_revision = 3;
      bridge.Submit(Snapshot(), metadata);
      wanted = context::ContextUpdateResult::kPublished;
      expected_epoch = 1; expected_revision = 3;
    }
  }
  gate->Release();
  const bool complete = Wait(bridge, wanted);
  const auto accepted = rime_plugin::EditorContextEpoch::Instance().LoadAccepted();
  bridge.Stop();
  std::cout << "race_complete=" << complete << " epoch=" << accepted.model_epoch
            << " revision=" << accepted.source_revision << '\n';
  return complete && accepted.model_epoch == expected_epoch &&
         accepted.source_revision == expected_revision ? 0 : 32;
}

int RunFakeTests() {
  for (auto mode : {RecoveryTransport::kBeforeAccept, RecoveryTransport::kLostAck,
                    RecoveryTransport::kHealthDrop, RecoveryTransport::kPermanent,
                    RecoveryTransport::kProtocol, RecoveryTransport::kIdentity}) {
    rime_plugin::EditorContextEpoch::Instance().Reset();
    auto transport = std::make_unique<RecoveryTransport>(mode);
    auto* fake = transport.get();
    context::ContextUpdateBridgeOptions options;
    options.readiness_timeout = 250ms;
    context::ContextUpdateBridge bridge(std::move(transport), options);
    bridge.Submit(Snapshot(), Metadata());
    const bool success = mode <= RecoveryTransport::kHealthDrop;
    if (!Wait(bridge, success ? context::ContextUpdateResult::kPublished :
                              context::ContextUpdateResult::kTransportError)) return 1;
    if (success && rime_plugin::EditorContextEpoch::Instance().Load() != 7) return 2;
    if (!success && rime_plugin::EditorContextEpoch::Instance().Load() != 0) return 3;
    if (mode == RecoveryTransport::kBeforeAccept && (fake->updates != 2 || fake->receipts != 1)) return 4;
    if (mode == RecoveryTransport::kLostAck && (fake->updates != 1 || fake->receipts != 1)) return 5;
    if (mode == RecoveryTransport::kHealthDrop && fake->updates != 1) return 6;
    if (mode == RecoveryTransport::kPermanent && fake->calls != 3) return 7;
    if (mode >= RecoveryTransport::kProtocol && fake->calls != 1) return 8;
    bridge.Stop();
  }
  for (int action = 0; action < 3; ++action) {
    auto transport = std::make_unique<RecoveryTransport>(RecoveryTransport::kBlocked);
    auto* fake = transport.get();
    context::ContextUpdateBridge bridge(std::move(transport));
    bridge.Submit(Snapshot(), Metadata());
    if (!fake->WaitBlocked()) { fake->Release(); return 9; }
    auto metadata = Metadata();
    metadata.source_revision = 2;
    if (action == 0) bridge.Submit(Snapshot(), metadata);
    if (action == 1) {
      metadata.secure = true;
      auto snapshot = Snapshot(); snapshot.before = L"SENSITIVE_SENTINEL";
      bridge.Submit(std::move(snapshot), metadata);
      if (rime_plugin::EditorContextEpoch::Instance().Load() != 0) { fake->Release(); return 10; }
    }
    if (action == 2) bridge.Invalidate();
    fake->Release();
    if (!Wait(bridge, action == 0 ? context::ContextUpdateResult::kPublished :
                      action == 1 ? context::ContextUpdateResult::kSecureContextCleared :
                                    context::ContextUpdateResult::kSuperseded)) return 11;
    bridge.Stop();
    if (fake->receipts != 0 || fake->updates != (action == 0 ? 1U : 0U) || fake->leaked) return 12;
  }
  // A stable process survives reconnection. A changed PID or a reused PID with
  // a different creation time cannot confirm the earlier process's epoch.
  for (const pipe::ServerProcessIdentity identity : {
           pipe::ServerProcessIdentity{77, 11}, {88, 11}, {77, 12}}) {
    const bool stable = identity == pipe::ServerProcessIdentity{77, 11};
    auto transport = std::make_unique<IdentityTransport>(identity);
    context::ContextUpdateBridge bridge(std::move(transport));
    bridge.Submit(Snapshot(), Metadata());
    if (!Wait(bridge, stable ? context::ContextUpdateResult::kPublished
                            : context::ContextUpdateResult::kSuperseded)) return 13;
    if (rime_plugin::EditorContextEpoch::Instance().Load() != (stable ? 7 : 0)) return 14;
    bridge.Stop();
  }
  std::cout << "recovery_boundary_cases=12 passed=12\n";
  return 0;
}
}  // namespace

int wmain(int argc, wchar_t** argv) {
  if (argc == 1) return RunFakeTests();
  // Explicit isolated pipe only; no default production pipe or editor access.
  if ((argc != 2 && argc != 3) ||
      std::wstring_view(argv[1]).find(L"\\\\.\\pipe\\nw-recovery-test-") != 0) return 20;
  const std::wstring_view scenario = argc == 3 ? argv[2] : L"single";
  if (scenario == L"supersede" || scenario == L"secure" ||
      scenario == L"barrier" || scenario == L"invalidate" || scenario == L"stop")
    return RunRace(argv[1], scenario);
  if (scenario != L"single" && scenario != L"restart-health" &&
      scenario != L"restart-collision") return 20;
  neural_weasel::context::ContextUpdateBridgeOptions options;
  options.pipe_query_timeout = std::chrono::milliseconds(200);
  options.readiness_timeout = std::chrono::milliseconds(1500);
  auto transport = std::make_unique<neural_weasel::context::NamedPipeContextUpdateTransport>(argv[1]);
  neural_weasel::context::ContextUpdateBridge bridge(std::move(transport), options);
  bridge.Submit(Snapshot(), Metadata());
  if (scenario == L"restart-health" || scenario == L"restart-collision") {
    const bool refused_old = Wait(bridge, neural_weasel::context::ContextUpdateResult::kSuperseded);
    const auto old_epoch = neural_weasel::rime_plugin::EditorContextEpoch::Instance().Load();
    std::cout << "restart_old_refused=" << refused_old << " old_epoch=" << old_epoch << '\n';
    if (!refused_old || old_epoch != 0) { bridge.Stop(); return 33; }
    auto metadata = Metadata(); metadata.source_revision = 2;
    bridge.Submit(Snapshot(), metadata);
    const bool published = Wait(bridge, neural_weasel::context::ContextUpdateResult::kPublished);
    const auto accepted = neural_weasel::rime_plugin::EditorContextEpoch::Instance().LoadAccepted();
    bridge.Stop();
    const std::uint64_t wanted_epoch = scenario == L"restart-collision" ? 102 : 1;
    std::cout << "restart_new_published=" << published << " epoch=" << accepted.model_epoch
              << " revision=" << accepted.source_revision << '\n';
    return published && accepted.model_epoch == wanted_epoch && accepted.source_revision == 2 ? 0 : 34;
  }
  const bool published = Wait(bridge, neural_weasel::context::ContextUpdateResult::kPublished);
  const auto epoch = neural_weasel::rime_plugin::EditorContextEpoch::Instance().Load();
  bridge.Stop();
  std::cout << "single_update_published=" << published << " epoch=" << epoch << '\n';
  return published && epoch == 1 ? 0 : 21;
}
