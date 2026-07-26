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

add_library(neural_weasel_tsf_context STATIC
  tsf/backend_fallback_state_machine.cc
  tsf/experimental_profile_plan.cc
  tsf/input_processor_profiles.cc
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

if(NEURAL_WEASEL_BUILD_NATIVE_TESTS)
  enable_testing()
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
endif()

if(NEURAL_WEASEL_BUILD_RIME_PLUGIN)
  set(RIME_ROOT "" CACHE PATH "Path to a librime source tree")

  if(NOT EXISTS "${RIME_ROOT}/src/rime/translator.h")
    message(FATAL_ERROR
      "RIME_ROOT must point to a librime source tree containing src/rime/translator.h")
  endif()
  find_package(nlohmann_json CONFIG REQUIRED)

  # librime 1.15.0 does not implement external plugin loading on Windows.
  # Produce a static integration library which the experimental Weasel build
  # must link and explicitly load before rime_api->initialize().
  add_library(neural_weasel_rime_plugin STATIC
    rime/ai_translator.cc
    rime/ai_translator_module.cc
  )
  target_include_directories(neural_weasel_rime_plugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${RIME_ROOT}/src
  )
  target_compile_definitions(neural_weasel_rime_plugin PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
  )
  target_link_libraries(neural_weasel_rime_plugin PUBLIC
    neural_weasel_context_bridge
    neural_weasel_pipe
    nlohmann_json::nlohmann_json
  )
endif()
