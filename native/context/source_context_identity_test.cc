#include "context/source_context_identity.h"

#include <iostream>

int main() {
  using neural_weasel::context::SourceContextIdentity;

  // A focus transition must make every stamp from the previous editor source
  // unusable, even when the new source later reaches the same revision.
  SourceContextIdentity focus_identity;
  if (!focus_identity.BeginFocus()) {
    std::cerr << "BeginFocus failed\n";
    return 1;
  }
  auto old_context = focus_identity.Capture();
  if (!old_context || !focus_identity.IsCurrent(*old_context)) {
    std::cerr << "initial context was not current\n";
    return 1;
  }
  if (!focus_identity.BeginFocus()) {
    std::cerr << "second BeginFocus failed\n";
    return 1;
  }
  auto new_context = focus_identity.Capture();
  if (!new_context || focus_identity.IsCurrent(*old_context) ||
      !focus_identity.IsCurrent(*new_context)) {
    std::cerr << "focus change did not invalidate old context\n";
    return 1;
  }

  // Captures from one focused source advance strictly monotonically. Once a
  // newer capture exists, an older revision from the same source is stale.
  SourceContextIdentity revision_identity;
  if (!revision_identity.BeginFocus()) {
    std::cerr << "revision BeginFocus failed\n";
    return 1;
  }
  auto first = revision_identity.Capture();
  auto second = revision_identity.Capture();
  if (!first || !second || second->revision <= first->revision ||
      revision_identity.IsCurrent(*first) ||
      !revision_identity.IsCurrent(*second)) {
    std::cerr << "revision was not monotonic\n";
    return 1;
  }

  // EndFocus is a hard invalidation barrier. Capture cannot reactivate a
  // cleared source, and a later focus cannot make the cleared stamp current.
  SourceContextIdentity cleared_identity;
  if (!cleared_identity.BeginFocus()) {
    std::cerr << "clear BeginFocus failed\n";
    return 1;
  }
  auto cleared_context = cleared_identity.Capture();
  if (!cleared_context) {
    std::cerr << "clear capture failed\n";
    return 1;
  }
  cleared_identity.EndFocus();
  if (cleared_identity.active() || cleared_identity.IsCurrent(*cleared_context) ||
      cleared_identity.Capture().has_value()) {
    std::cerr << "cleared context resurrected\n";
    return 1;
  }
  if (!cleared_identity.BeginFocus()) {
    std::cerr << "refocus after clear failed\n";
    return 1;
  }
  auto refocused_context = cleared_identity.Capture();
  if (!refocused_context || cleared_identity.IsCurrent(*cleared_context) ||
      !cleared_identity.IsCurrent(*refocused_context)) {
    std::cerr << "cleared context resurrected after refocus\n";
    return 1;
  }

  return 0;
}
