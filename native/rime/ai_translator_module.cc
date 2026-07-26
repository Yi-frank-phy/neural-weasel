#include <rime_api.h>
#include <rime/common.h>
#include <rime/registry.h>

#include "rime/ai_translator.h"
#include "rime/editor_context_epoch.h"

using namespace rime;

static void rime_ai_translator_initialize() {
  LOG(INFO) << "registering components from module 'ai_translator'.";
  Registry::instance().Register(
      "ai_translator",
      new Component<neural_weasel::rime_plugin::AiTranslator>);
}

static void rime_ai_translator_finalize() {
  neural_weasel::rime_plugin::EditorContextEpoch::Instance().Reset();
}

RIME_REGISTER_MODULE(ai_translator)

