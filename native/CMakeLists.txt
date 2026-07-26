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

add_library(neural_weasel_pipe STATIC
  pipe/named_pipe_client.cc
)
target_include_directories(neural_weasel_pipe PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_definitions(neural_weasel_pipe PRIVATE
  UNICODE
  _UNICODE
  WIN32_LEAN_AND_MEAN
  NOMINMAX
)
target_link_libraries(neural_weasel_pipe PRIVATE advapi32)

add_library(neural_weasel_tsf_context STATIC
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
target_link_libraries(neural_weasel_tsf_context PRIVATE msctf ole32)

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
    rime/editor_context_epoch.cc
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
    neural_weasel_pipe
    nlohmann_json::nlohmann_json
  )
endif()
