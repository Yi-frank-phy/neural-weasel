#include "context/context_update_bridge.h"

#include <Windows.h>

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "rime/editor_context_epoch.h"

namespace {

using neural_weasel::context::ContextUpdateBridge;
using neural_weasel::context::ContextUpdateBridgeOptions;
using neural_weasel::context::ContextUpdateMetadata;
using neural_weasel::context::ContextUpdateResult;
using neural_weasel::context::ContextUpdateTransport;
using neural_weasel::pipe::QueryResult;
using neural_weasel::pipe::QueryStatus;
using neural_weasel::rime_plugin::EditorContextEpoch;
using neural_weasel::tsf::SurroundingTextSnapshot;

QueryResult Ok(std::string payload) {
  return {QueryStatus::kOk, std::move(payload), ERROR_SUCCESS};
}

class BlockingFakeTransport final : public ContextUpdateTransport {
 public:
  QueryResult TryQuery(
      std::string_view request,
      std::chrono::milliseconds) override {
    std::unique_lock lock(mutex_);
    requests_.emplace_back(request);
    if (request.find("\"type\":\"context_update\"") !=
            std::string_view::npos &&
        request.find("\"request_id\":\"ctx-1\"") !=
            std::string_view::npos) {
      first_request_received_ = true;
      condition_.notify_all();
      condition_.wait(lock, [this] { return release_first_response_; });
      return Ok(
          "{\"type\":\"context_update\",\"ok\":true,\"accepted\":true,"
          "\"context_epoch\":5,\"client_context_epoch\":1,"
          "\"request_id\":\"ctx-1\"}");
    }
    if (request.find("\"type\":\"focus\"") != std::string_view::npos &&
        request.find("\"request_id\":\"ctx-2\"") !=
            std::string_view::npos) {
      return Ok(
          "{\"type\":\"focus\",\"ok\":true,\"accepted\":true,"
          "\"session_id\":\"test-session\",\"focused\":true,\"secure\":true,"
          "\"request_id\":\"ctx-2\"}");
    }
    if (request.find("\"type\":\"context_update\"") !=
            std::string_view::npos &&
        request.find("\"request_id\":\"ctx-3\"") !=
            std::string_view::npos) {
      return Ok(
          "{\"type\":\"context_update\",\"ok\":true,\"accepted\":true,"
          "\"context_epoch\":1,\"client_context_epoch\":3,"
          "\"request_id\":\"ctx-3\"}");
    }
    if (request.find("\"type\":\"health\"") != std::string_view::npos &&
        request.find("\"request_id\":\"ctx-3\"") !=
            std::string_view::npos) {
      return Ok(
          "{\"type\":\"health\",\"ok\":true,\"ready\":true,"
          "\"context_epoch\":1,\"request_id\":\"ctx-3\"}");
    }
    return {QueryStatus::kProtocolError, {}, ERROR_INVALID_DATA};
  }

  bool WaitForFirstRequest(std::chrono::milliseconds timeout) {
    std::unique_lock lock(mutex_);
    return condition_.wait_for(
        lock, timeout, [this] { return first_request_received_; });
  }

  void ReleaseFirstResponse() {
    std::lock_guard lock(mutex_);
    release_first_response_ = true;
    condition_.notify_all();
  }

  std::vector<std::string> Requests() const {
    std::lock_guard lock(mutex_);
    return requests_;
  }

 private:
  mutable std::mutex mutex_;
  std::condition_variable condition_;
  bool first_request_received_ = false;
  bool release_first_response_ = false;
  std::vector<std::string> requests_;
};

int Fail(const char* message) {
  std::cerr << message << '\n';
  return 1;
}

}  // namespace

int main() {
  // A restarted service can legitimately assign epoch one after the previous
  // process published a much larger local epoch.
  EditorContextEpoch::Instance().Publish(100);
  auto transport = std::make_unique<BlockingFakeTransport>();
  BlockingFakeTransport* fake = transport.get();

  ContextUpdateBridgeOptions options;
  options.readiness_timeout = std::chrono::milliseconds(500);
  ContextUpdateBridge bridge(std::move(transport), options);

  SurroundingTextSnapshot old_normal;
  old_normal.before = L"old public context";
  old_normal.result = S_OK;
  ContextUpdateMetadata normal_metadata;
  normal_metadata.application_id = L"test.exe";
  normal_metadata.session_id = "test-session";
  normal_metadata.secure = false;
  normal_metadata.partial = false;
  if (bridge.Submit(std::move(old_normal), normal_metadata) != 1) {
    return Fail("first sequence was not one");
  }
  if (!fake->WaitForFirstRequest(std::chrono::milliseconds(250))) {
    return Fail("first request was not submitted on the worker");
  }

  SurroundingTextSnapshot sensitive;
  sensitive.before = L"SECRET_CONTEXT_SENTINEL";
  sensitive.after = L"SECRET_AFTER_SENTINEL";
  sensitive.partial = true;
  sensitive.result = S_OK;
  ContextUpdateMetadata sensitive_metadata;
  sensitive_metadata.application_id = L"test.exe";
  sensitive_metadata.session_id = "test-session";
  sensitive_metadata.secure = true;
  if (bridge.Submit(std::move(sensitive), sensitive_metadata) != 2) {
    return Fail("second sequence was not two");
  }
  if (EditorContextEpoch::Instance().Load() != 0) {
    return Fail("secure Submit did not clear the local epoch immediately");
  }
  fake->ReleaseFirstResponse();

  const auto secure_deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(1);
  bool nonzero_epoch_during_secure_cleanup = false;
  while (std::chrono::steady_clock::now() < secure_deadline &&
         bridge.last_result() !=
             ContextUpdateResult::kSecureContextCleared) {
    nonzero_epoch_during_secure_cleanup |=
        EditorContextEpoch::Instance().Load() != 0;
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  if (bridge.last_result() != ContextUpdateResult::kSecureContextCleared ||
      EditorContextEpoch::Instance().Load() != 0 ||
      nonzero_epoch_during_secure_cleanup) {
    return Fail("secure cleanup was not acknowledged without an epoch");
  }

  SurroundingTextSnapshot normal;
  normal.before = L"public context after restart";
  normal.partial = false;
  normal.before_reached_region_boundary = true;
  normal.after_reached_region_boundary = true;
  normal.result = S_OK;
  if (bridge.Submit(std::move(normal), normal_metadata) != 3) {
    return Fail("third sequence was not three");
  }
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(1);
  while (std::chrono::steady_clock::now() < deadline &&
         bridge.last_result() != ContextUpdateResult::kPublished) {
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  if (bridge.last_result() != ContextUpdateResult::kPublished ||
      EditorContextEpoch::Instance().Load() != 1) {
    return Fail("restarted service epoch one was not published");
  }

  bridge.Stop();
  const std::vector<std::string> requests = fake->Requests();
  if (requests.empty()) {
    return Fail("transport captured no requests");
  }
  const auto denied = std::find_if(
      requests.begin(), requests.end(), [](const std::string& request) {
        return request.find("\"type\":\"focus\"") != std::string::npos;
      });
  if (denied == requests.end()) {
    return Fail("transport captured no secure focus cleanup");
  }
  const std::string& denied_request = *denied;
  if (denied_request.find("SECRET_CONTEXT_SENTINEL") != std::string::npos ||
      denied_request.find("SECRET_AFTER_SENTINEL") != std::string::npos) {
    return Fail("secure source text reached the serialized request");
  }
  if (denied_request.find("\"request_id\":\"ctx-2\"") ==
          std::string::npos ||
      denied_request.find("\"type\":\"focus\"") == std::string::npos ||
      denied_request.find("\"secure\":true") == std::string::npos ||
      denied_request.find("\"before\"") != std::string::npos ||
      denied_request.find("\"after\"") != std::string::npos) {
    return Fail("secure request or string request_id was malformed");
  }
  return 0;
}
