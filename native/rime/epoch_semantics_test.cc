#include "rime/epoch_semantics.h"
#include "rime/candidate_page_retry.h"
#include "rime/neural_refresh_key.h"

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
  using neural_weasel::rime_plugin::FirstPageRetryDelayMs;
  using neural_weasel::rime_plugin::ShouldRetryFirstPage;
  if (FirstPageRetryDelayMs(0) != 25 || FirstPageRetryDelayMs(1) != 50 ||
      !ShouldRetryFirstPage(false, 0) ||
      !ShouldRetryFirstPage(false, 3) ||
      ShouldRetryFirstPage(false, 4) ||
      ShouldRetryFirstPage(true, 1)) {
    std::cerr << "first-page retry scheduling semantics mismatch\n";
    return 1;
  }
  return 0;
}
