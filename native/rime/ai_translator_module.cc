#include <rime_api.h>
#include <rime/common.h>
#include <rime/registry.h>

#include "rime/ai_translator.h"
#include "rime/bilingual_key_processor.h"
#include "rime/editor_context_epoch.h"

using namespace rime;

static bool rime_ai_translator_components_initialized = false;

static void rime_ai_translator_initialize() {
  if (rime_ai_translator_components_initialized) {
    return;
  }
  LOG(INFO) << "registering components from module 'ai_translator'.";
  Registry::instance().Register(
      "ai_translator",
      new Component<neural_weasel::rime_plugin::AiTranslator>);
  Registry::instance().Register(
      "bilingual_key_processor",
      new Component<neural_weasel::rime_plugin::BilingualKeyProcessor>);
  rime_ai_translator_components_initialized = true;
}

static void rime_ai_translator_finalize() {
  if (!rime_ai_translator_components_initialized) {
    return;
  }
  neural_weasel::rime_plugin::EditorContextEpoch::Instance().Reset();
  rime_ai_translator_components_initialized = false;
}

void rime_register_module_ai_translator_explicit() {
  if (RimeFindModule("ai_translator") != nullptr) {
    return;
  }
  static RimeModule module = {0};
  if (!module.data_size) {
    RIME_STRUCT_INIT(RimeModule, module);
    module.module_name = "ai_translator";
    module.initialize = rime_ai_translator_initialize;
    module.finalize = rime_ai_translator_finalize;
  }
  RimeRegisterModule(&module);
}

void rime_require_module_ai_translator() {
  rime_register_module_ai_translator_explicit();
}

void rime_initialize_module_ai_translator_explicit() {
  rime_ai_translator_initialize();
}

void rime_finalize_module_ai_translator_explicit() {
  rime_ai_translator_finalize();
}

