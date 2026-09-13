#include <rime_api.h>

#include <chrono>
#include <cstdio>
#include <string>
#include <string_view>
#include <thread>
#include <utility>

#include <nlohmann/json.hpp>

#include "pipe/named_pipe_client.h"
#include "rime/neural_refresh_key.h"

using Json = nlohmann::json;

void rime_register_module_ai_translator_explicit();

namespace {

bool service_ready = true;

std::string CommitForKey(RimeApi* api, RimeSessionId session, int keycode) {
  api->process_key(session, keycode, 0);
  RIME_STRUCT(RimeCommit, commit);
  if (!api->get_commit(session, &commit)) {
    return {};
  }
  const std::string text = commit.text ? commit.text : "";
  api->free_commit(&commit);
  return text;
}

int CandidateCount(RimeApi* api, RimeSessionId session) {
  RIME_STRUCT(RimeContext, context);
  if (!api->get_context(session, &context)) {
    return 0;
  }
  const int count = context.menu.num_candidates;
  api->free_context(&context);
  return count;
}

bool HasCandidateText(RimeApi* api, RimeSessionId session,
                      std::string_view expected) {
  RIME_STRUCT(RimeContext, context);
  if (!api->get_context(session, &context)) {
    return false;
  }
  bool found = false;
  for (int index = 0; index < context.menu.num_candidates; ++index) {
    const char* text = context.menu.candidates[index].text;
    if (text && expected == text) {
      found = true;
      break;
    }
  }
  api->free_context(&context);
  return found;
}

std::string PropertyValue(RimeApi* api, RimeSessionId session,
                          const char* name) {
  char value[32] = {};
  api->get_property(session, name, value, sizeof(value));
  return value;
}

}  // namespace

namespace neural_weasel::pipe {

NamedPipeClient::NamedPipeClient(std::wstring name, std::uint32_t maximum)
    : pipe_name_(std::move(name)), max_payload_bytes_(maximum) {}

NamedPipeClient::~NamedPipeClient() = default;

QueryResult NamedPipeClient::TryQuery(std::string_view payload,
                                      std::chrono::milliseconds timeout) {
  if (!service_ready) {
    std::this_thread::sleep_for(timeout);
    return {QueryStatus::kTimeout, {}, ERROR_SEM_TIMEOUT};
  }
  Json response = Json::parse(payload);
  response["type"] = "candidate_page";
  response["ok"] = true;
  response["candidate_set_id"] = "integration-set";
  response["background_pending"] = false;
  response["has_more"] = false;
  response["candidates"] = Json::array();
  const bool latin = response["language_mode"] == "latin_first";
  const char* latin_candidates[] = {"apple", "application", "apply", "apart",
                                    "april"};
  const char* han_candidates[] = {
      u8"\u6697", u8"\u57c3", u8"\u6c28", u8"\u77ee",
      u8"\u554a", u8"\u5965", u8"\u963f"};
  const int count = latin ? 5 : 7;
  for (int index = 0; index < count; ++index) {
    response["candidates"].push_back(
        {{"text", latin ? latin_candidates[index] : han_candidates[index]},
         {"consumed_keys", response["raw_keys"].get<std::string>().size()},
         {"constraint_kind", latin ? "latin_prefix" : "pinyin"},
         {"script", latin ? "latin" : "han"}});
  }
  return {QueryStatus::kOk, response.dump(), ERROR_SUCCESS};
}

}  // namespace neural_weasel::pipe

int main(int argc, char** argv) {
  if (argc != 2) {
    return 2;
  }
  auto* api = rime_get_api();
  RIME_STRUCT(RimeTraits, traits);
  traits.shared_data_dir = traits.user_data_dir = traits.log_dir = argv[1];
  traits.app_name = "rime.neural_candidate_integration";
  const char* modules[] = {"default", "ai_translator", nullptr};
  traits.modules = modules;
  api->setup(&traits);
  rime_register_module_ai_translator_explicit();
  api->initialize(&traits);
  const auto session = api->create_session();
  if (!session || !api->select_schema(session, "neural_weasel")) {
    return 3;
  }

  int failures = 0;
  auto reset_latin = [&] {
    api->clear_composition(session);
    api->set_option(session, "ascii_mode", true);
    service_ready = true;
    api->process_key(session, 'a', 0);
  };

  reset_latin();
  if (CommitForKey(api, session, ' ') != "apple ") {
    std::fprintf(stderr, "Space did not commit the default highlighted item\n");
    ++failures;
  }

  reset_latin();
  api->process_key(session, 0xff54, 0);  // Down, handled by Rime's selector.
  if (PropertyValue(api, session, "neural_presentation_locked") != "1") {
    std::fprintf(stderr, "Down did not record explicit selection intent\n");
    ++failures;
  }
  if (CommitForKey(api, session, ' ') != "application ") {
    std::fprintf(stderr, "Down then Space did not commit the highlighted item\n");
    ++failures;
  }

  reset_latin();
  api->process_key(session, 0xff54, 0);  // Down.
  api->process_key(session, 0xff52, 0);  // Up, explicitly back to item zero.
  if (CommitForKey(api, session, ' ') != "apple ") {
    std::fprintf(stderr,
                 "Down then Up then Space lost explicit selection intent\n");
    ++failures;
  }

  reset_latin();
  api->process_key(session, 0xff54, 0);  // Down.
  api->process_key(session, 'p', 0);     // New input revision.
  if (PropertyValue(api, session, "neural_presentation_locked") != "0") {
    std::fprintf(stderr, "new input did not clear explicit selection intent\n");
    ++failures;
  }

  api->clear_composition(session);
  api->set_option(session, "ascii_mode", false);
  service_ready = false;
  api->process_key(session, 'a', 0);
  std::uint64_t elapsed_ms = 0;
  unsigned int attempts = 0;
  bool presentation_ready = false;
  while (neural_weasel::rime_plugin::ShouldRetryFirstPage(
      presentation_ready, elapsed_ms)) {
    elapsed_ms +=
        neural_weasel::rime_plugin::FirstPageRetryDelayMs(attempts++);
    service_ready = elapsed_ms >= 300;
    presentation_ready = api->process_key(
        session, neural_weasel::rime_plugin::kNeuralRefreshKeycode, 0);
  }
  if (!presentation_ready || CandidateCount(api, session) != 7 ||
      elapsed_ms >=
          neural_weasel::rime_plugin::kNeuralFirstPageRetryBudgetMs) {
    std::fprintf(stderr,
                 "elapsed-budget refresh did not recover a late first page\n");
    ++failures;
  }
  if (!HasCandidateText(api, session, u8"\u554a")) {
    std::fprintf(stderr,
                 "late single-syllable page did not expose exact a -> U+554A\n");
    ++failures;
  }
  if (CommitForKey(api, session, '5') != u8"\u554a") {
    std::fprintf(stderr,
                 "numbered selection did not commit exact a -> U+554A\n");
    ++failures;
  }

  api->destroy_session(session);
  api->finalize();
  return failures == 0 ? 0 : 1;
}

