#pragma once

#include <rime/processor.h>

namespace neural_weasel::rime_plugin {

class BilingualKeyProcessor final : public ::rime::Processor {
 public:
  explicit BilingualKeyProcessor(const ::rime::Ticket& ticket)
      : ::rime::Processor(ticket) {}

  ::rime::ProcessResult ProcessKeyEvent(
      const ::rime::KeyEvent& key_event) override;
};

}  // namespace neural_weasel::rime_plugin

