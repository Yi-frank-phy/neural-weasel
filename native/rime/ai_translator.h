#pragma once

#include <atomic>
#include <chrono>
#include <cstdint>
#include <string>

#include <rime/translation.h>
#include <rime/translator.h>

#include "pipe/named_pipe_client.h"
#include "rime/epoch_semantics.h"

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
  std::uint64_t revision_ = 0;
  std::chrono::milliseconds query_timeout_{6};
  pipe::NamedPipeClient pipe_;
};

}  // namespace neural_weasel::rime_plugin
