#include "rime/ai_translator.h"

#include <algorithm>
#include <cstdarg>
#include <utility>

#include <nlohmann/json.hpp>
#include <rime/candidate.h>
#include <rime/context.h>
#include <rime/engine.h>
#include <rime/segmentation.h>

#include "rime/editor_context_epoch.h"

namespace neural_weasel::rime_plugin {
namespace {

constexpr std::size_t kCandidateCount = 5;

using Json = nlohmann::json;

// Temporary metadata-only target-machine diagnostic. Never pass raw keys,
// candidate text, surrounding text, window titles, or capability values here.
void TraceAiTranslator(const wchar_t* format, ...) {
  wchar_t local_app_data[MAX_PATH] = {};
  const DWORD length = GetEnvironmentVariableW(
      L"LOCALAPPDATA", local_app_data, _countof(local_app_data));
  if (length == 0 || length >= _countof(local_app_data)) {
    return;
  }

  wchar_t path[MAX_PATH] = {};
  if (swprintf_s(path,
                 L"%s\\NeuralWeasel\\Experimental\\ai-translator.log",
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
        line, L"tick=%llu pid=%lu tid=%lu %s\r\n", GetTickCount64(),
        GetCurrentProcessId(), GetCurrentThreadId(), message);
    if (line_chars > 0) {
      DWORD written = 0;
      WriteFile(file, line,
                static_cast<DWORD>(line_chars * sizeof(wchar_t)), &written,
                nullptr);
    }
  }
  CloseHandle(file);
}

std::string CandidateComment(const Json& item) {
  std::string comment;
  if (item.contains("pinyin") && item["pinyin"].is_string()) {
    comment = item["pinyin"].get<std::string>();
  }
  if (item.value("coverage", false)) {
    if (!comment.empty()) {
      comment += " ";
    }
    comment += "[coverage]";
  }
  return comment;
}

}  // namespace

std::atomic<std::uint64_t> AiTranslator::next_session_id_{1};

AiTranslator::AiTranslator(const ::rime::Ticket& ticket)
    : ::rime::Translator(ticket),
      session_id_(next_session_id_.fetch_add(1, std::memory_order_relaxed)) {}

::rime::an<::rime::Translation> AiTranslator::Query(
    const std::string& input,
    const ::rime::Segment& segment) {
  TraceAiTranslator(
      L"event=query enter input-len=%llu segment-start=%llu segment-end=%llu "
      L"abc=%d",
      static_cast<unsigned long long>(input.size()),
      static_cast<unsigned long long>(segment.start),
      static_cast<unsigned long long>(segment.end),
      segment.HasTag("abc") ? 1 : 0);
  if (!segment.HasTag("abc") || input.empty()) {
    TraceAiTranslator(L"event=query result=tag-or-empty");
    return nullptr;
  }
  engine_->context()->set_property("neural_candidate_fresh", "0");

  try {
    const AcceptedEditorContext context_identity =
        EditorContextEpoch::Instance().LoadAccepted();
    TraceAiTranslator(
        L"event=query identity-valid=%d model-epoch=%llu source-revision=%llu",
        context_identity.valid() ? 1 : 0,
        static_cast<unsigned long long>(context_identity.model_epoch),
        static_cast<unsigned long long>(context_identity.source_revision));
    if (!context_identity.valid() || context_identity.model_epoch == 0) {
      // Never let epoch 0 implicitly select a previous application's model
      // snapshot. Ordinary Rime candidates remain available while editor
      // context is absent or still refreshing.
      return nullptr;
    }

    const std::uint64_t request_revision = ++revision_;
    const std::string request_session_id = std::to_string(session_id_);
    const Json request = {
        {"type", "query_candidates"},
        {"session_id", request_session_id},
        {"revision", request_revision},
        {"context_epoch", context_identity.model_epoch},
        {"context_session", context_identity.source_capability},
        {"source_revision", context_identity.source_revision},
        {"raw_keys", input},
        {"candidate_count", kCandidateCount},
    };

    auto result = pipe_.TryQuery(request.dump(), query_timeout_);
    if (!result) {
      TraceAiTranslator(L"event=query result=pipe-failure status=%d error=%lu",
                        static_cast<int>(result.status), result.win32_error);
      return nullptr;
    }

    const Json response = Json::parse(result.payload);
    if (response.value("type", "") != "candidates" ||
        response.value("session_id", std::string{}) != request_session_id ||
        response.value("revision", std::uint64_t{0}) != request_revision ||
        !IsResponseEpochAcceptable(
            context_identity.model_epoch,
            response.value("context_epoch", std::uint64_t{0})) ||
        !response.contains("candidates") ||
        !response["candidates"].is_array()) {
      TraceAiTranslator(L"event=query result=response-rejected");
      return nullptr;
    }
    TraceAiTranslator(
        L"event=query response-candidates=%llu suppressed=%d",
        static_cast<unsigned long long>(response["candidates"].size()),
        engine_->context()->get_option("_neural_completion_suppressed") ? 1
                                                                         : 0);
    const std::string input_mode =
        response.value("input_mode", std::string{"ambiguous"});
    engine_->context()->set_property("neural_input_mode", input_mode);
    if (engine_->context()->get_option("_neural_completion_suppressed")) {
      TraceAiTranslator(L"event=query result=suppressed");
      return nullptr;
    }

    auto translation = ::rime::New<::rime::FifoTranslation>();
    std::size_t accepted = 0;
    for (const auto& item : response["candidates"]) {
      if (accepted >= kCandidateCount || !item.is_object() ||
          !item.contains("text") || !item["text"].is_string()) {
        continue;
      }
      const std::size_t consumed = item.value("consumed_keys", std::size_t{0});
      const std::size_t segment_size = segment.end - segment.start;
      TraceAiTranslator(
          L"event=query candidate consumed=%llu input-len=%llu "
          L"segment-size=%llu",
          static_cast<unsigned long long>(consumed),
          static_cast<unsigned long long>(input.size()),
          static_cast<unsigned long long>(segment_size));
      if (consumed == 0 || consumed > input.size() ||
          consumed > segment_size) {
        continue;
      }

      const std::string constraint_kind =
          item.value("constraint_kind", std::string{});
      const std::string candidate_type =
          constraint_kind == "literal"
              ? "neural_literal"
              : constraint_kind == "latin_prefix" ? "neural_latin"
                                                   : "neural_pinyin";
      auto candidate = ::rime::New<::rime::SimpleCandidate>(
          candidate_type, segment.start, segment.start + consumed,
          item["text"].get<std::string>(), CandidateComment(item));
      if (item.contains("score") && item["score"].is_number()) {
        candidate->set_quality(item["score"].get<double>());
      }
      translation->Append(candidate);
      ++accepted;
    }
    if (accepted == 0) {
      TraceAiTranslator(L"event=query result=no-accepted-candidates");
      return nullptr;
    }
    TraceAiTranslator(L"event=query result=accepted count=%llu",
                      static_cast<unsigned long long>(accepted));
    engine_->context()->set_property("neural_candidate_fresh", "1");
    return translation;
  } catch (...) {
    TraceAiTranslator(L"event=query result=exception");
    return nullptr;
  }
}

}  // namespace neural_weasel::rime_plugin
