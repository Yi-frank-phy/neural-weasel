#include "rime/epoch_semantics.h"
#include "rime/candidate_page_retry.h"

#include <iostream>

int main() {
  using neural_weasel::rime_plugin::ShouldKeepCandidatePagePending;
  using neural_weasel::rime_plugin::IsResponseEpochAcceptable;
  if (!IsResponseEpochAcceptable(0, 0) ||
      !IsResponseEpochAcceptable(0, 1) ||
      !IsResponseEpochAcceptable(0, 99) ||
      !IsResponseEpochAcceptable(7, 7) ||
      IsResponseEpochAcceptable(7, 6) ||
      IsResponseEpochAcceptable(7, 8)) {
    std::cerr << "native context_epoch semantics mismatch\n";
    return 1;
  }
  if (!ShouldKeepCandidatePagePending(0, "candidate_page_timeout", true) ||
      ShouldKeepCandidatePagePending(1, "candidate_page_timeout", true) ||
      ShouldKeepCandidatePagePending(0, "candidate_page_timeout", false) ||
      ShouldKeepCandidatePagePending(0, "internal_error", true)) {
    std::cerr << "retryable page-zero timeout pending semantics mismatch\n";
    return 1;
  }
  return 0;
}
