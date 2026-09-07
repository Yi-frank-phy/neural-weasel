#include <rime_api.h>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>
void rime_register_module_ai_translator_explicit();
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  auto* api = rime_get_api();
  RIME_STRUCT(RimeTraits, traits);
  traits.shared_data_dir = traits.user_data_dir = traits.log_dir = argv[1];
  traits.app_name = "rime.neural_acceptance";
  const char* modules[] = {"default", "ai_translator", nullptr};
  traits.modules = modules;
  api->setup(&traits);
  rime_register_module_ai_translator_explicit();
  api->initialize(&traits);
  auto session = api->create_session();
  if (!session || !api->select_schema(session, "neural_weasel")) return 3;
  int failures = 0;
  for (int latin = 0; latin < 2; ++latin) {
    api->clear_composition(session);
    api->set_option(session, "ascii_mode", latin != 0);
    std::string output;
    bool idle = true;
    for (char key : std::string("10023456789")) {
      bool handled = api->process_key(session, key, 0);
      if (!handled) output += key;  // The host inserts unhandled keys.
      RIME_STRUCT(RimeCommit, commit);
      if (api->get_commit(session, &commit)) {
        if (commit.text) output += commit.text;
        api->free_commit(&commit);
      }
      RIME_STRUCT(RimeContext, ctx);
      if (api->get_context(session, &ctx)) {
        idle = idle && ctx.composition.length == 0 && ctx.menu.num_candidates == 0;
        api->free_context(&ctx);
      }
    }
    bool passed = idle && output == "10023456789";
    printf("idle-digits latin=%d passed=%d\n", latin, passed);
    if (!passed) ++failures;
  }
  for (int route = 0; route < 2; ++route) {
    for (int action = 0; action < 7; ++action) {
      api->clear_composition(session);
      api->set_option(session, "ascii_mode", false);
      if (route == 0) api->set_option(session, "ascii_mode", true);
      else {
        api->process_key(session, 0xffe1, 0);
        api->process_key(session, 0xffe1, 1 << 30);
      }
      for (char key : std::string("hel")) api->process_key(session, key, 0);
      RIME_STRUCT(RimeContext, ctx);
      api->get_context(session, &ctx);
      for (int retry = 0; ctx.menu.num_candidates < 5 && retry < 16; ++retry) {
        api->free_context(&ctx);
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
        api->process_key(session, 0xfdd0, 0);
        api->get_context(session, &ctx);
      }
      int index = action == 6 ? 1 : action == 0 ? 0 : action - 1;
      if (ctx.menu.num_candidates != 5) {
        ++failures;
        api->free_context(&ctx);
        continue;
      }
      std::string expected = ctx.menu.candidates[index].text;
      if (action == 0 || action == 6) expected += ' ';
      api->free_context(&ctx);
      if (action == 6) api->process_key(session, 0xff54, 0);
      api->process_key(session, action == 0 || action == 6 ? ' ' : '0' + action, 0);
      RIME_STRUCT(RimeCommit, commit);
      bool present = api->get_commit(session, &commit);
      bool passed = present && commit.text && expected == commit.text;
      printf("route=%s action=%d expected=[%s] actual=[%s] passed=%d\n",
             route ? "shift" : "option", action, expected.c_str(),
             present && commit.text ? commit.text : "<no commit>", passed);
      if (!passed) ++failures;
      if (present) api->free_commit(&commit);
    }
  }
  api->destroy_session(session);
  api->finalize();
  return failures ? 1 : 0;
}
