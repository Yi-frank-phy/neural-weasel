#include "context_ipc_protocol.h"

#include <cassert>
#include <cstdint>
#include <string_view>
#include <vector>

using neural_weasel::context::ContextFrame;
using neural_weasel::context::ContextFrameAcceptResult;
using neural_weasel::context::ContextFrameKind;
using neural_weasel::context::ContextFrameReceiver;
using neural_weasel::context::ContextScopeLabel;
using neural_weasel::context::EncodeContextFrame;
using neural_weasel::context::kContextFrameHeaderBytes;
using neural_weasel::context::kMaxContextFrameBytes;

namespace {

ContextFrame ValidFrame(std::uint64_t revision = 1) {
  ContextFrame frame;
  frame.kind = ContextFrameKind::kContext;
  frame.scope_label = ContextScopeLabel::kNormal;
  frame.source_pid = 4242;
  for (std::size_t index = 0; index < frame.source_capability.size(); ++index) {
    frame.source_capability[index] = static_cast<std::uint8_t>(index + 1);
  }
  frame.revision = revision;
  frame.before = u"ordinary before";
  frame.after = u"after";
  return frame;
}

std::string_view Bytes(const std::vector<std::uint8_t>& frame) {
  return {reinterpret_cast<const char*>(frame.data()), frame.size()};
}

}  // namespace

int main() {
  ContextFrameReceiver receiver;

  // oversized payload rejected
  std::vector<std::uint8_t> oversized(kMaxContextFrameBytes + 1, 0);
  assert(receiver.Accept(Bytes(oversized)) ==
         ContextFrameAcceptResult::kOversized);

  // valid frame accepted
  const ContextFrame valid = ValidFrame(7);
  std::vector<std::uint8_t> encoded;
  assert(EncodeContextFrame(valid, &encoded));
  assert(encoded.size() ==
         kContextFrameHeaderBytes +
             2U * (valid.before.size() + valid.after.size()));
  ContextFrame accepted;
  assert(receiver.Accept(Bytes(encoded), &accepted) ==
         ContextFrameAcceptResult::kAccepted);
  assert(accepted.revision == 7);
  assert(accepted.source_capability == valid.source_capability);
  assert(accepted.scope_label == ContextScopeLabel::kNormal);
  assert(accepted.before == valid.before);
  assert(accepted.after == valid.after);
  assert(receiver.latest_revision() == 7);

  // malformed frame rejected: declared lengths no longer match the bytes.
  auto malformed = encoded;
  malformed.pop_back();
  assert(receiver.Accept(Bytes(malformed)) ==
         ContextFrameAcceptResult::kMalformed);
  assert(receiver.latest_revision() == 7);

  // stale revision ignored without replacing accepted source state.
  const ContextFrame stale = ValidFrame(6);
  std::vector<std::uint8_t> stale_encoded;
  assert(EncodeContextFrame(stale, &stale_encoded));
  assert(receiver.Accept(Bytes(stale_encoded)) ==
         ContextFrameAcceptResult::kStale);
  assert(receiver.latest_revision() == 7);

  return 0;
}
