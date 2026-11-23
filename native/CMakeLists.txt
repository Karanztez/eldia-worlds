cmake_minimum_required(VERSION 3.20)

project(eldia_worlds_native VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(FetchContent)
FetchContent_Declare(
    endstone
    GIT_REPOSITORY https://github.com/EndstoneMC/endstone.git
    GIT_TAG v0.11.2
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(endstone)

endstone_add_plugin(${PROJECT_NAME}
    src/plugin.cpp
    src/sigscan.cpp
    src/dimension_hook.cpp
)

target_include_directories(${PROJECT_NAME} PRIVATE src)

if (MSVC)
    target_compile_options(${PROJECT_NAME} PRIVATE /W4 /utf-8)
else ()
    target_compile_options(${PROJECT_NAME} PRIVATE -Wall -Wextra)
endif ()
