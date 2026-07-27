#include "rime/ai_translator.h"

#include <algorithm>
#include <exception>
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
  if (!segment.HasTag("abc") || input.empty()) {
    return nullptr;
  }

  const std::uint64_t request_revision = ++revision_;
  const std::uint64_t context_epoch = EditorContextEpoch::Instance().Load();
  const std::string request_session_id = std::to_string(session_id_);
  const Json request = {
      {"type", "query_candidates"},
      {"session_id", request_session_id},
      {"revision", request_revision},
      {"context_epoch", context_epoch},
      {"raw_keys", input},
      {"candidate_count", kCandidateCount},
  };

  auto result = pipe_.TryQuery(request.dump(), query_timeout_);
  if (!result) {
    return nullptr;
  }

  try {
    const Json response = Json::parse(result.payload);
    if (response.value("type", "") != "candidates" ||
        response.value("session_id", std::string{}) != request_session_id ||
        response.value("revision", std::uint64_t{0}) != request_revision ||
        response.value("context_epoch", std::uint64_t{0}) != context_epoch ||
        !response.contains("candidates") ||
        !response["candidates"].is_array()) {
      return nullptr;
    }
    const std::string input_mode =
        response.value("input_mode", std::string{"ambiguous"});
    engine_->context()->set_property("neural_input_mode", input_mode);
    if (engine_->context()->get_option("_neural_completion_suppressed")) {
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
      return nullptr;
    }
    return translation;
  } catch (const std::exception&) {
    return nullptr;
  }
}

}  // namespace neural_weasel::rime_plugin
