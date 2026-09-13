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
      FirstPageRetryDelayMs(2) != 100 || FirstPageRetryDelayMs(4) != 200 ||
      FirstPageRetryDelayMs(20) != 200 ||
      !ShouldRetryFirstPage(false, 35) ||
      !ShouldRetryFirstPage(false, 120) ||
      !ShouldRetryFirstPage(false, 2499) ||
      ShouldRetryFirstPage(false, 2500) ||
      ShouldRetryFirstPage(true, 50)) {
    std::cerr << "first-page retry scheduling semantics mismatch\n";
    return 1;
  }
  return 0;
}
