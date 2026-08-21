#include "context/context_capture_broker.h"

int main() {
  // Construction/destruction must not start backend work. Linking this test
  // forces the complete broker object (and its server-only dependencies) to be
  // resolved by MSVC without creating a live pipe in the test process.
  neural_weasel::context::ContextCaptureBroker broker;
  broker.Stop();
  return 0;
}
