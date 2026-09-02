#include "rime/ai_translator.h"

#include <algorithm>
#include <cstdarg>
#include <limits>
#include <utility>

#include <nlohmann/json.hpp>
#include <rime/candidate.h>
#include <rime/context.h>
#include <rime/engine.h>
#include <rime/segmentation.h>

#include "rime/editor_context_epoch.h"

namespace neural_weasel::rime_plugin {
namespace {

constexpr std::size_t kChinesePageSize = 9;
constexpr std::size_t kLatinPageSize = 5;

using Json = nlohmann::json;

// Metadata-only target-machine diagnostic. Never pass raw keys, candidate text,
// surrounding text, window titles, context capabilities, or candidate ids here.
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
  if (item.contains("pinyin") && item["pinyin"].is_string()) {
    return item["pinyin"].get<std::string>();
  }
  return {};
}

std::string CurrentLanguageMode(::rime::Context* context) {
  const std::string value = context->get_property("neural_language_mode");
  if (value == "latin_first") {
    return value;
  }
  if (value != "chinese_first") {
    context->set_property("neural_language_mode", "chinese_first");
  }
  return "chinese_first";
}

std::uint32_t RequestedPage(::rime::Context* context) {
  const std::string value = context->get_property("neural_requested_page");
  if (value.empty()) {
    return 0;
  }
  try {
    const unsigned long parsed = std::stoul(value);
    if (parsed > std::numeric_limits<std::uint32_t>::max()) {
      return 0;
    }
    return static_cast<std::uint32_t>(parsed);
  } catch (...) {
    return 0;
  }
}

bool IsSourceBoundaryChange(const std::string& captured_session,
                            const AcceptedEditorContext& latest) {
  if (captured_session.empty()) {
    // An epoch-0 page is intentionally frozen even when ordinary editor context
    // becomes available later. The next input revision may capture it.
    return false;
  }
  return !latest.valid() || latest.source_capability != captured_session;
}

}  // namespace

std::atomic<std::uint64_t> AiTranslator::next_session_id_{1};

AiTranslator::AiTranslator(const ::rime::Ticket& ticket)
    : ::rime::Translator(ticket),
      session_id_(next_session_id_.fetch_add(1, std::memory_order_relaxed)) {
  if (engine_ && engine_->context()) {
    auto* context = engine_->context();
    observed_composing_ = context->IsComposing();
    context_update_connection_ = context->update_notifier().connect(
        [this](::rime::Context* updated) { OnContextUpdate(updated); });
  }
}

AiTranslator::~AiTranslator() {
  context_update_connection_.disconnect();
}

void AiTranslator::ResetCompositionBoundary() {
  // Keep composition_revision_ monotonic. The next non-empty composition must
  // receive a new revision even if it reuses the same raw keys, language mode,
  // and editor source as the composition that was just committed/cancelled.
  force_new_revision_ = true;
  composition_input_.clear();
  candidate_set_id_.clear();
  current_page_index_ = 0;
  current_has_more_ = false;
  frozen_pages_.clear();
}

void AiTranslator::OnContextUpdate(::rime::Context* context) {
  const bool composing = context && context->IsComposing();
  if (!composing && (observed_composing_ || !composition_input_.empty())) {
    ResetCompositionBoundary();
    TraceAiTranslator(L"event=composition-boundary");
  }
  observed_composing_ = composing;
}

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

  auto* context = engine_->context();
  if (!context) {
    return nullptr;
  }
  context->set_property("neural_candidate_fresh", "0");

  try {
    const std::string language_mode = CurrentLanguageMode(context);
    const AcceptedEditorContext latest_context =
        EditorContextEpoch::Instance().LoadAccepted();
    const bool source_boundary =
        composition_revision_ > 0 &&
        IsSourceBoundaryChange(context_session_, latest_context);
    const bool new_revision = force_new_revision_ || composition_revision_ == 0 ||
                              composition_input_ != input ||
                              composition_mode_ != language_mode ||
                              source_boundary;
    if (new_revision) {
      ++composition_revision_;
      force_new_revision_ = false;
      composition_input_ = input;
      composition_mode_ = language_mode;
      if (latest_context.valid()) {
        context_epoch_ = latest_context.model_epoch;
        context_session_ = latest_context.source_capability;
        source_revision_ = latest_context.source_revision;
      } else {
        context_epoch_ = 0;
        context_session_.clear();
        source_revision_ = 0;
      }
      candidate_set_id_.clear();
      current_page_index_ = 0;
      current_has_more_ = false;
      frozen_pages_.clear();
      context->set_property("neural_requested_page", "0");
      context->set_property("neural_page_index", "0");
      context->set_property("neural_has_more", "0");
      TraceAiTranslator(
          L"event=revision created revision=%llu context-epoch=%llu mode=%d",
          static_cast<unsigned long long>(composition_revision_),
          static_cast<unsigned long long>(context_epoch_),
          language_mode == "latin_first" ? 1 : 0);
    }

    std::uint32_t requested_page = RequestedPage(context);
    if (language_mode == "latin_first") {
      requested_page = 0;
      context->set_property("neural_requested_page", "0");
    }
    if (requested_page > current_page_index_ + 1U) {
      requested_page = current_page_index_;
      context->set_property("neural_requested_page",
                            std::to_string(requested_page));
    }

    std::string page_payload;
    const auto cached = frozen_pages_.find(requested_page);
    if (cached != frozen_pages_.end()) {
      page_payload = cached->second;
    } else {
      if (requested_page > 0 &&
          (requested_page != current_page_index_ + 1U ||
           !current_has_more_ || candidate_set_id_.empty())) {
        const auto current = frozen_pages_.find(current_page_index_);
        if (current == frozen_pages_.end()) {
          return nullptr;
        }
        requested_page = current_page_index_;
        context->set_property("neural_requested_page",
                              std::to_string(requested_page));
        page_payload = current->second;
      } else {
        const std::string request_session_id = std::to_string(session_id_);
        Json request = {
            {"type", "query_candidate_page"},
            {"session_id", request_session_id},
            {"composition_revision", composition_revision_},
            {"context_epoch", context_epoch_},
            {"language_mode", language_mode},
            {"raw_keys", input},
            {"page_index", requested_page},
        };
        if (context_epoch_ > 0) {
          request["context_session"] = context_session_;
          request["source_revision"] = source_revision_;
        }
        if (requested_page > 0) {
          request["candidate_set_id"] = candidate_set_id_;
        }

        const auto timeout = requested_page == 0 ? query_timeout_
                                                 : next_page_timeout_;
        auto result = pipe_.TryQuery(request.dump(), timeout);
        if (!result) {
          TraceAiTranslator(
              L"event=page result=pipe-failure page=%lu status=%d error=%lu",
              static_cast<unsigned long>(requested_page),
              static_cast<int>(result.status), result.win32_error);
          const auto current = frozen_pages_.find(current_page_index_);
          if (current == frozen_pages_.end()) {
            return nullptr;
          }
          requested_page = current_page_index_;
          context->set_property("neural_requested_page",
                                std::to_string(requested_page));
          page_payload = current->second;
        } else {
          const Json response = Json::parse(result.payload);
          const std::string response_set =
              response.value("candidate_set_id", std::string{});
          const bool set_matches =
              requested_page == 0 ? !response_set.empty()
                                  : response_set == candidate_set_id_;
          if (response.value("type", "") != "candidate_page" ||
              !response.value("ok", false) ||
              response.value("session_id", std::string{}) !=
                  request_session_id ||
              response.value("composition_revision", std::uint64_t{0}) !=
                  composition_revision_ ||
              response.value("context_epoch", std::uint64_t{0}) !=
                  context_epoch_ ||
              response.value("language_mode", std::string{}) !=
                  language_mode ||
              response.value("page_index", std::uint32_t{0}) !=
                  requested_page ||
              !set_matches || !response.contains("candidates") ||
              !response["candidates"].is_array()) {
            TraceAiTranslator(L"event=page result=response-rejected page=%lu",
                              static_cast<unsigned long>(requested_page));
            const auto current = frozen_pages_.find(current_page_index_);
            if (current == frozen_pages_.end()) {
              return nullptr;
            }
            requested_page = current_page_index_;
            context->set_property("neural_requested_page",
                                  std::to_string(requested_page));
            page_payload = current->second;
          } else {
            if (requested_page == 0) {
              candidate_set_id_ = response_set;
            }
            current_page_index_ = requested_page;
            current_has_more_ = response.value("has_more", false);
            page_payload = response.dump();
            frozen_pages_[requested_page] = page_payload;
            TraceAiTranslator(
                L"event=page frozen page=%lu count=%llu has-more=%d",
                static_cast<unsigned long>(requested_page),
                static_cast<unsigned long long>(response["candidates"].size()),
                current_has_more_ ? 1 : 0);
          }
        }
      }
    }

    const Json page = Json::parse(page_payload);
    current_page_index_ = page.value("page_index", current_page_index_);
    current_has_more_ = page.value("has_more", false);
    if (page.contains("candidate_set_id") &&
        page["candidate_set_id"].is_string()) {
      const std::string response_set =
          page["candidate_set_id"].get<std::string>();
      if (candidate_set_id_.empty()) {
        candidate_set_id_ = response_set;
      }
    }
    context->set_property("neural_page_index",
                          std::to_string(current_page_index_));
    context->set_property("neural_requested_page",
                          std::to_string(current_page_index_));
    context->set_property("neural_has_more", current_has_more_ ? "1" : "0");

    auto translation = ::rime::New<::rime::FifoTranslation>();
    const std::size_t page_limit = language_mode == "latin_first"
                                       ? kLatinPageSize
                                       : kChinesePageSize;
    std::size_t accepted = 0;
    for (const auto& item : page["candidates"]) {
      if (accepted >= page_limit || !item.is_object() ||
          !item.contains("text") || !item["text"].is_string()) {
        continue;
      }
      const std::size_t consumed = item.value("consumed_keys", std::size_t{0});
      const std::size_t segment_size = segment.end - segment.start;
      if (consumed == 0 || consumed > input.size() ||
          consumed > segment_size) {
        continue;
      }

      const std::string constraint_kind =
          item.value("constraint_kind", std::string{});
      const std::string script = item.value("script", std::string{});
      if (language_mode == "latin_first" && script != "latin") {
        continue;
      }
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
      TraceAiTranslator(L"event=query result=no-accepted-candidates page=%lu",
                        static_cast<unsigned long>(current_page_index_));
      return nullptr;
    }
    context->set_property("neural_candidate_fresh", "1");
    TraceAiTranslator(L"event=query result=accepted page=%lu count=%llu",
                      static_cast<unsigned long>(current_page_index_),
                      static_cast<unsigned long long>(accepted));
    return translation;
  } catch (...) {
    TraceAiTranslator(L"event=query result=exception");
    return nullptr;
  }
}

}  // namespace neural_weasel::rime_plugin
