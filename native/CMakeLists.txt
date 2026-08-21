cmake_minimum_required(VERSION 3.24)

project(neural_weasel_native LANGUAGES CXX)

if(NOT WIN32)
  message(FATAL_ERROR "The native integration targets require Windows and TSF.")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(NEURAL_WEASEL_BUILD_RIME_PLUGIN
       "Build the static ai_translator integration library" OFF)
option(NEURAL_WEASEL_BUILD_NATIVE_TESTS
       "Build standalone native boundary tests" OFF)
option(NEURAL_WEASEL_BUILD_PROFILE_TOOL
       "Build the experimental TSF profile registration tool" ON)
option(NEURAL_WEASEL_BUILD_SESSION_ACTIVATOR
       "Build the current-session experimental profile activator" ON)

add_library(neural_weasel_pipe STATIC
  pipe/named_pipe_client.cc
)
target_include_directories(neural_weasel_pipe PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_definitions(neural_weasel_pipe PRIVATE
  UNICODE
  _UNICODE
  WINVER=0x0A00
  _WIN32_WINNT=0x0A00
  WIN32_LEAN_AND_MEAN
  NOMINMAX
)
target_link_libraries(neural_weasel_pipe PRIVATE advapi32)

add_library(neural_weasel_context_protocol STATIC
  context/context_ipc_protocol.cc
)
target_include_directories(neural_weasel_context_protocol
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_definitions(neural_weasel_context_protocol PRIVATE
  UNICODE
  _UNICODE
  WIN32_LEAN_AND_MEAN
  NOMINMAX
)

add_library(neural_weasel_tsf_context STATIC
  tsf/backend_fallback_state_machine.cc
  tsf/experimental_profile_plan.cc
  tsf/input_processor_profiles.cc
  tsf/input_scope_policy.cc
  tsf/surrounding_text_edit_session.cc
)
target_include_directories(neural_weasel_tsf_context
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_definitions(neural_weasel_tsf_context PRIVATE
  UNICODE
  _UNICODE
  WIN32_LEAN_AND_MEAN
  NOMINMAX
)
target_link_libraries(neural_weasel_tsf_context PRIVATE ole32 oleaut32 uuid)

add_library(neural_weasel_capture_pipeline STATIC
  context/capture_pipeline.cc
  context/source_context_identity.cc
)
target_include_directories(neural_weasel_capture_pipeline
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_definitions(neural_weasel_capture_pipeline PRIVATE
  UNICODE
  _UNICODE
  WIN32_LEAN_AND_MEAN
  NOMINMAX
)
target_link_libraries(neural_weasel_capture_pipeline PUBLIC bcrypt)

add_library(neural_weasel_tsf_context_sender STATIC
  tsf/context_capture_client.cc
)
target_include_directories(neural_weasel_tsf_context_sender
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_definitions(neural_weasel_tsf_context_sender PRIVATE
  UNICODE
  _UNICODE
  WINVER=0x0A00
  _WIN32_WINNT=0x0A00
  WIN32_LEAN_AND_MEAN
  NOMINMAX
)
target_link_libraries(neural_weasel_tsf_context_sender PUBLIC
  neural_weasel_context_protocol
  advapi32
)

if(NEURAL_WEASEL_BUILD_PROFILE_TOOL)
  add_executable(NeuralWeaselProfileTool
    profile_tool/profile_tool.cpp
  )
  set_target_properties(NeuralWeaselProfileTool PROPERTIES
    OUTPUT_NAME "NeuralWeaselProfileTool"
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
  )
  target_include_directories(NeuralWeaselProfileTool PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
  )
  target_compile_definitions(NeuralWeaselProfileTool PRIVATE
    UNICODE
    _UNICODE
    WINVER=0x0A00
    _WIN32_WINNT=0x0A00
    WIN32_LEAN_AND_MEAN
    NOMINMAX
  )
  target_link_libraries(NeuralWeaselProfileTool PRIVATE
    advapi32
    ole32
    uuid
  )
endif()

if(NEURAL_WEASEL_BUILD_SESSION_ACTIVATOR)
  add_executable(NeuralWeaselSessionActivator
    session_activator/session_activator.cpp
  )
  set_target_properties(NeuralWeaselSessionActivator PROPERTIES
    OUTPUT_NAME "NeuralWeaselSessionActivator"
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>"
  )
  target_include_directories(NeuralWeaselSessionActivator PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
  )
  target_compile_definitions(NeuralWeaselSessionActivator PRIVATE
    UNICODE
    _UNICODE
    WINVER=0x0A00
    _WIN32_WINNT=0x0A00
    WIN32_LEAN_AND_MEAN
    NOMINMAX
  )
  target_link_libraries(NeuralWeaselSessionActivator PRIVATE
    ole32
    uuid
  )
endif()

# Heavy model-service forwarding is server-side only. This target may own a
# worker thread and synchronous request/response transport because it is never
# linked into the editor-hosted TSF DLL.
add_library(neural_weasel_context_bridge STATIC
  context/context_update_bridge.cc
  rime/editor_context_epoch.cc
)
target_include_directories(neural_weasel_context_bridge
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_definitions(neural_weasel_context_bridge PRIVATE
  UNICODE
  _UNICODE
  WIN32_LEAN_AND_MEAN
  NOMINMAX
)
target_link_libraries(neural_weasel_context_bridge PUBLIC
  neural_weasel_pipe
)

add_library(neural_weasel_context_broker STATIC
  context/context_capture_broker.cc
)
target_include_directories(neural_weasel_context_broker
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_definitions(neural_weasel_context_broker PRIVATE
  UNICODE
  _UNICODE
  WINVER=0x0A00
  _WIN32_WINNT=0x0A00
  WIN32_LEAN_AND_MEAN
  NOMINMAX
)
target_link_libraries(neural_weasel_context_broker PUBLIC
  neural_weasel_context_protocol
  neural_weasel_context_bridge
  advapi32
)

# The TSF adapter deliberately has no dependency on neural_weasel_context_bridge
# or neural_weasel_pipe. It can only classify/capture and push bounded frames.
add_library(neural_weasel_weasel_context_adapter STATIC
  tsf/weasel_context_adapter.cc
)
target_include_directories(neural_weasel_weasel_context_adapter
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)
target_compile_definitions(neural_weasel_weasel_context_adapter PRIVATE
  UNICODE
  _UNICODE
  WINVER=0x0A00
  _WIN32_WINNT=0x0A00
  WIN32_LEAN_AND_MEAN
  NOMINMAX
)
target_link_libraries(neural_weasel_weasel_context_adapter PUBLIC
  neural_weasel_tsf_context_sender
  neural_weasel_capture_pipeline
  neural_weasel_tsf_context
  ole32
)

add_library(neural_weasel_bilingual_key_semantics STATIC
  rime/bilingual_key_semantics.cc
)
target_include_directories(neural_weasel_bilingual_key_semantics
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}
)

if(NEURAL_WEASEL_BUILD_NATIVE_TESTS)
  enable_testing()

  add_executable(neural_weasel_context_protocol_test
    context/context_ipc_protocol_test.cc
  )
  target_link_libraries(neural_weasel_context_protocol_test PRIVATE
    neural_weasel_context_protocol
  )
  add_test(NAME neural_weasel_context_protocol_test
           COMMAND neural_weasel_context_protocol_test)

  add_executable(neural_weasel_source_context_identity_test
    context/source_context_identity_test.cc
  )
  target_link_libraries(neural_weasel_source_context_identity_test PRIVATE
    neural_weasel_capture_pipeline
  )
  add_test(NAME neural_weasel_source_context_identity_test
           COMMAND neural_weasel_source_context_identity_test)

  add_executable(neural_weasel_surrounding_text_edit_session_test
    tsf/surrounding_text_edit_session_test.cc
  )
  target_compile_definitions(neural_weasel_surrounding_text_edit_session_test PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
  )
  target_link_libraries(neural_weasel_surrounding_text_edit_session_test PRIVATE
    neural_weasel_tsf_context
  )
  add_test(NAME neural_weasel_surrounding_text_edit_session_test
           COMMAND neural_weasel_surrounding_text_edit_session_test)

  add_executable(neural_weasel_context_broker_test
    context/context_capture_broker_test.cc
  )
  target_compile_definitions(neural_weasel_context_broker_test PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
  )
  target_link_libraries(neural_weasel_context_broker_test PRIVATE
    neural_weasel_context_broker
  )
  add_test(NAME neural_weasel_context_broker_test
           COMMAND neural_weasel_context_broker_test)

  add_executable(neural_weasel_context_bridge_test
    context/context_update_bridge_test.cc
  )
  target_compile_definitions(neural_weasel_context_bridge_test PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
  )
  target_link_libraries(neural_weasel_context_bridge_test PRIVATE
    neural_weasel_context_bridge
  )
  add_test(NAME neural_weasel_context_bridge_test
           COMMAND neural_weasel_context_bridge_test)

  add_executable(neural_weasel_fallback_state_machine_test
    tsf/backend_fallback_state_machine_test.cc
  )
  target_compile_definitions(neural_weasel_fallback_state_machine_test PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
  )
  target_link_libraries(neural_weasel_fallback_state_machine_test PRIVATE
    neural_weasel_tsf_context
  )
  add_test(NAME neural_weasel_fallback_state_machine_test
           COMMAND neural_weasel_fallback_state_machine_test)

  add_executable(neural_weasel_input_scope_policy_test
    tsf/input_scope_policy_test.cc
  )
  target_compile_definitions(neural_weasel_input_scope_policy_test PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
  )
  target_link_libraries(neural_weasel_input_scope_policy_test PRIVATE
    neural_weasel_capture_pipeline
    neural_weasel_tsf_context
  )
  add_test(NAME neural_weasel_input_scope_policy_test
           COMMAND neural_weasel_input_scope_policy_test)

  add_executable(neural_weasel_bilingual_key_semantics_test
    rime/bilingual_key_semantics_test.cc
  )
  target_compile_definitions(neural_weasel_bilingual_key_semantics_test PRIVATE
    NEURAL_WEASEL_KEY_FIXTURE_PATH="${CMAKE_CURRENT_SOURCE_DIR}/../tests/fixtures/key_state_vectors.tsv"
  )
  target_link_libraries(neural_weasel_bilingual_key_semantics_test PRIVATE
    neural_weasel_bilingual_key_semantics
  )
  add_test(NAME neural_weasel_bilingual_key_semantics_test
           COMMAND neural_weasel_bilingual_key_semantics_test)

  add_executable(neural_weasel_epoch_semantics_test
    rime/epoch_semantics_test.cc
  )
  target_include_directories(neural_weasel_epoch_semantics_test PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
  )
  add_test(NAME neural_weasel_epoch_semantics_test
           COMMAND neural_weasel_epoch_semantics_test)
endif()

if(NEURAL_WEASEL_BUILD_RIME_PLUGIN)
  set(RIME_ROOT "" CACHE PATH "Path to a librime source tree")

  if(NOT EXISTS "${RIME_ROOT}/src/rime/translator.h")
    message(FATAL_ERROR
      "RIME_ROOT must point to a librime source tree containing src/rime/translator.h")
  endif()
  find_package(nlohmann_json CONFIG REQUIRED)

  set(RIME_DATA_DIR "rime-data")
  set(RIME_PLUGINS_DIR "rime-plugins")
  set(RIME_GENERATED_INCLUDE_DIR
      "${CMAKE_CURRENT_BINARY_DIR}/rime_generated")
  file(MAKE_DIRECTORY "${RIME_GENERATED_INCLUDE_DIR}/rime")
  configure_file(
    "${RIME_ROOT}/src/rime/build_config.h.in"
    "${RIME_GENERATED_INCLUDE_DIR}/rime/build_config.h"
  )

  add_library(neural_weasel_rime_plugin STATIC
    rime/ai_translator.cc
    rime/ai_translator_module.cc
    rime/bilingual_key_processor.cc
  )
  target_include_directories(neural_weasel_rime_plugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${RIME_GENERATED_INCLUDE_DIR}
    ${RIME_ROOT}/src
    ${RIME_ROOT}/include
  )
  target_compile_definitions(neural_weasel_rime_plugin PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
  )
  target_link_libraries(neural_weasel_rime_plugin PUBLIC
    neural_weasel_context_bridge
    neural_weasel_bilingual_key_semantics
    neural_weasel_pipe
    nlohmann_json::nlohmann_json
  )
endif()
