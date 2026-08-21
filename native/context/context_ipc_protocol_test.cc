#include "context_ipc_protocol.h"

#include <cassert>

using neural_weasel::context::ContextFrame;
using neural_weasel::context::ContextFrameReceiver;

static ContextFrame ValidFrame() {
  return {"capability", 1, "NORMAL", 4, 4, "abcd"};
}

int main() {
  ContextFrameReceiver receiver;

  auto oversized = ValidFrame();
  oversized.payload.assign(5000, 'x');
  assert(!receiver.Accept(oversized));

  auto malformed = ValidFrame();
  malformed.source_capability.clear();
  assert(!receiver.Accept(malformed));

  assert(receiver.Accept(ValidFrame()));

  auto stale = ValidFrame();
  assert(!receiver.Accept(stale));

  auto newer = ValidFrame();
  newer.revision = 2;
  assert(receiver.Accept(newer));

  return 0;
}
