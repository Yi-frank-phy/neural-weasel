#include "context/capture_pipeline.h"

#include <utility>

namespace neural_weasel::context {
namespace {

const char* ScopeLabel(tsf::InputScopeState state) noexcept {
  switch (state) {
    case tsf::InputScopeState::kPrivate:
      return "PRIVATE";
    case tsf::InputScopeState::kPassword:
      return "PASSWORD";
    case tsf::InputScopeState::kNormal:
    default:
      return "NORMAL";
  }
}

}  // namespace

std::optional<CaptureContextSnapshot> CaptureWithPolicy(
    const tsf::InputScopePolicyResult& policy,
    SourceContextIdentity& identity,
    const SurroundingTextCapture& capture) {
  if (policy.state == tsf::InputScopeState::kPassword ||
      !policy.allow_capture || !capture) {
    return std::nullopt;
  }

  const auto stamp = identity.Capture();
  if (!stamp) {
    return std::nullopt;
  }

  auto snapshot = capture();
  if (FAILED(snapshot.result) || !identity.IsCurrent(*stamp)) {
    return std::nullopt;
  }

  CaptureContextMetadata metadata;
  metadata.source_capability = stamp->capability;
  metadata.revision = stamp->revision;
  metadata.scope_label = ScopeLabel(policy.state);
  metadata.before_length =
      static_cast<std::uint32_t>(snapshot.before.size());
  metadata.after_length = static_cast<std::uint32_t>(snapshot.after.size());

  CaptureContextSnapshot result;
  result.metadata = std::move(metadata);
  result.snapshot = std::move(snapshot);
  result.allow_persistence = policy.allow_persistence;
  return result;
}

}  // namespace neural_weasel::context
