#pragma once

#include <memory>

namespace neural_weasel::context {

// Heavy context ownership lives in NeuralWeaselServer.exe, never in the TSF
// DLL. The broker accepts bounded one-way editor frames and forwards only the
// latest valid source snapshot to the existing model-service bridge.
class ContextCaptureBroker final {
 public:
  ContextCaptureBroker();
  ~ContextCaptureBroker();

  ContextCaptureBroker(const ContextCaptureBroker&) = delete;
  ContextCaptureBroker& operator=(const ContextCaptureBroker&) = delete;

  bool Start() noexcept;
  void Stop() noexcept;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace neural_weasel::context
