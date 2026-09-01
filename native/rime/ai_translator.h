#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <map>
#include <string>

#include <rime/translation.h>
#include <rime/translator.h>

#include "pipe/named_pipe_client.h"

namespace neural_weasel::rime_plugin {

class AiTranslator final : public ::rime::Translator {
 public:
  explicit AiTranslator(const ::rime::Ticket& ticket);

  ::rime::an<::rime::Translation> Query(
      const std::string& input,
      const ::rime::Segment& segment) override;

 private:
  static std::atomic<std::uint64_t> next_session_id_;

  std::uint64_t session_id_;
  std::uint64_t composition_revision_ = 0;
  std::string composition_input_;
  std::string composition_mode_ = "chinese_first";
  std::uint64_t context_epoch_ = 0;
  std::string context_session_;
  std::uint64_t source_revision_ = 0;
  std::string candidate_set_id_;
  std::uint32_t current_page_index_ = 0;
  bool current_has_more_ = false;
  std::map<std::uint32_t, std::string> frozen_pages_;
  std::chrono::milliseconds query_timeout_{50};
  std::chrono::milliseconds next_page_timeout_{120};
  pipe::NamedPipeClient pipe_;
};

}  // namespace neural_weasel::rime_plugin
