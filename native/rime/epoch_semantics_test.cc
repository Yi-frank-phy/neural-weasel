#include "rime/epoch_semantics.h"

#include <iostream>

int main() {
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
  return 0;
}
