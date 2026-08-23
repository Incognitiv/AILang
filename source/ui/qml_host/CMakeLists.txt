cmake_minimum_required(VERSION 3.20)
project(AILangQmlHost LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Qml Quick)

add_library(ailang_qml_host STATIC qml_host.cpp qml_host.h)
target_include_directories(ailang_qml_host PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(ailang_qml_host PUBLIC
    Qt6::Core
    Qt6::Gui
    Qt6::Qml
    Qt6::Quick
)

if(MSVC)
    target_compile_options(ailang_qml_host PRIVATE
        /W4
        /WX
        /permissive-
    )
else()
    target_compile_options(ailang_qml_host PRIVATE
        -Wall
        -Wextra
        -Werror
        -Wpedantic
        -Wshadow
        -Wconversion
        -Wsign-conversion
        -Wformat=2
        -Wnull-dereference
    )
endif()

if(AILANG_QML_DEMO_C)
    add_executable(qml_host_demo ${AILANG_QML_DEMO_C})
    target_link_libraries(qml_host_demo PRIVATE ailang_qml_host)
    set_target_properties(qml_host_demo PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    )
endif()
