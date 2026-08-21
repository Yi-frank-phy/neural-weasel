#include "context_ipc_protocol.h"

namespace neural_weasel::context {

bool ValidateContextFrame(const ContextFrame& frame) {
  if (frame.source_capability.empty() || frame.scope_label.empty()) {
    return false;
  }
  if (frame.revision == 0) {
    return false;
  }
  if (frame.payload.size() > kMaxContextPayloadBytes) {
    return false;
  }
  return true;
}

bool ContextFrameReceiver::Accept(ContextFrame frame) {
  if (!ValidateContextFrame(frame)) {
    return false;
  }
  if (frame.revision <= latest_revision_) {
    return false;
  }
  latest_revision_ = frame.revision;
  last_frame_ = std::move(frame);
  return true;
}

}  // namespace neural_weasel::context
