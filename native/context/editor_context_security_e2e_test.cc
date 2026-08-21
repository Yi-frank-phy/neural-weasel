#include "context/context_ipc_protocol.h"
#include "tsf/input_scope_policy.h"

#include <iostream>
#include <string>

using neural_weasel::context::ContextFrame;
using neural_weasel::context::ContextFrameReceiver;
using neural_weasel::tsf::ClassifyInputScope;
using neural_weasel::tsf::InputScopeState;

namespace {

int failures = 0;

void Check(bool condition, const char* message) {
  if (!condition) {
    std::cerr << "FAIL: " << message << '\n';
    ++failures;
  }
}

ContextFrame Frame(std::string capability,
                   std::uint64_t revision,
                   std::string label,
                   std::string payload) {
  ContextFrame frame;
  frame.source_capability = std::move(capability);
  frame.revision = revision;
  frame.scope_label = std::move(label);
  frame.before_length = static_cast<std::uint32_t>(payload.size());
  frame.after_length = 0;
  frame.payload = std::move(payload);
  return frame;
}

}  // namespace

int main() {
  const auto normal_policy = ClassifyInputScope(0);
  Check(normal_policy.state == InputScopeState::kNormal,
        "ordinary input must classify NORMAL");
  Check(normal_policy.allow_capture && normal_policy.allow_prediction,
        "NORMAL must allow capture and prediction");

  const auto private_policy = ClassifyInputScope(0x00000002UL);
  Check(private_policy.state == InputScopeState::kPrivate,
        "private input must classify PRIVATE");
  Check(private_policy.allow_capture && private_policy.allow_prediction,
        "PRIVATE must still allow ephemeral capture and prediction");
  Check(!private_policy.allow_persistence,
        "PRIVATE must forbid persistence");

  const auto password_policy = ClassifyInputScope(0x00000001UL);
  Check(password_policy.state == InputScopeState::kPassword,
        "password input must classify PASSWORD");
  Check(!password_policy.allow_capture && !password_policy.allow_prediction &&
            !password_policy.allow_persistence,
        "PASSWORD must be zero-capture and zero-persistence");

  {
    ContextFrameReceiver receiver;
    Check(receiver.Accept(Frame("focus-a", 1, "NORMAL", "ordinary-context")),
          "NORMAL context must reach the receiver");
    Check(receiver.last_frame().payload == "ordinary-context",
          "accepted NORMAL context must be the current frame");
  }

  {
    ContextFrameReceiver receiver;
    Check(receiver.Accept(Frame("focus-a", 1, "PRIVATE", "private-context")),
          "PRIVATE context must be usable ephemerally");
  }

  {
    ContextFrameReceiver receiver;
    Check(!receiver.Accept(Frame("focus-a", 1, "PASSWORD", "secret")),
          "PASSWORD context must never enter the receiver state");
  }

  {
    ContextFrameReceiver receiver;
    Check(receiver.Accept(Frame("focus-a", 1, "NORMAL", "old-focus-1")),
          "first focus revision must be accepted");
    Check(receiver.Accept(Frame("focus-a", 2, "NORMAL", "old-focus-2")),
          "old focus revisions must increase monotonically");
    Check(receiver.Accept(Frame("focus-b", 1, "NORMAL", "new-focus")),
          "new focus capability must start a fresh revision sequence");
    Check(!receiver.Accept(Frame("focus-a", 3, "NORMAL", "resurrected-old")),
          "retired focus capability must never resurrect");
  }

  {
    ContextFrameReceiver receiver;
    Check(receiver.Accept(Frame("typing", 1, "NORMAL", "r1")),
          "rapid typing revision 1 must be accepted");
    Check(receiver.Accept(Frame("typing", 3, "NORMAL", "r3")),
          "newest rapid-typing revision must be accepted");
    Check(!receiver.Accept(Frame("typing", 2, "NORMAL", "r2-stale")),
          "stale rapid-typing revision must be rejected");
    Check(receiver.last_frame().revision == 3 &&
              receiver.last_frame().payload == "r3",
          "latest rapid-typing revision must remain current");
  }

  if (failures != 0) {
    std::cerr << failures << " editor-context security regression(s) failed\n";
    return 1;
  }
  return 0;
}
