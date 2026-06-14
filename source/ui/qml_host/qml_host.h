#ifndef AILANG_QML_HOST_H
#define AILANG_QML_HOST_H

#include <stdint.h>

#ifdef _WIN32
#define AIL_QML_API __declspec(dllexport)
#else
#define AIL_QML_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

AIL_QML_API int64_t ail_qml_create(void);
AIL_QML_API int64_t ail_qml_load_file(int64_t host_id, const char *path);
AIL_QML_API int64_t ail_qml_set_context_string(
    int64_t host_id,
    const char *name,
    const char *value);
AIL_QML_API int64_t ail_qml_set_context_int(
    int64_t host_id,
    const char *name,
    int64_t value);
AIL_QML_API int64_t ail_qml_exec(int64_t host_id);
AIL_QML_API void ail_qml_destroy(int64_t host_id);
AIL_QML_API const char *ail_qml_last_error(int64_t host_id);
AIL_QML_API int64_t ail_qml_run_file(const char *path);
AIL_QML_API int64_t ail_qml_run_default(void);

#ifdef __cplusplus
}
#endif

#endif
