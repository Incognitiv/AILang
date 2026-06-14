#ifndef AIL_UI_BACKEND_H
#define AIL_UI_BACKEND_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    AIL_UI_PLATFORM_UNKNOWN = 0,
    AIL_UI_PLATFORM_WINDOWS = 1,
    AIL_UI_PLATFORM_LINUX = 2,
    AIL_UI_PLATFORM_FREEBSD = 3
};

enum {
    AIL_UI_EVENT_NONE = 0,
    AIL_UI_EVENT_CLOSE = 1,
    AIL_UI_EVENT_RESIZE = 2,
    AIL_UI_EVENT_MOUSE_MOVE = 3,
    AIL_UI_EVENT_MOUSE_DOWN = 4,
    AIL_UI_EVENT_MOUSE_UP = 5,
    AIL_UI_EVENT_KEY_DOWN = 6,
    AIL_UI_EVENT_KEY_UP = 7
};

int64_t ail_ui_init(void);
void ail_ui_shutdown(void);

int64_t ail_ui_open_window(const char *title, int64_t width, int64_t height);
int64_t ail_ui_open_borderless_window(const char *title, int64_t width, int64_t height);
void ail_ui_close_window(int64_t window);
void ail_ui_minimize_window(int64_t window);
void ail_ui_toggle_maximize_window(int64_t window);
int64_t ail_ui_window_alive(int64_t window);

int64_t ail_ui_platform(void);
int64_t ail_ui_window_width_px(int64_t window);
int64_t ail_ui_window_height_px(int64_t window);
int64_t ail_ui_window_scale_milli(int64_t window);
int64_t ail_ui_window_text_scale_milli(int64_t window);
int64_t ail_ui_window_maximized(int64_t window);

int64_t ail_ui_poll_event(int64_t window);
int64_t ail_ui_event_x(void);
int64_t ail_ui_event_y(void);
int64_t ail_ui_event_key(void);
int64_t ail_ui_event_width_px(void);
int64_t ail_ui_event_height_px(void);

void ail_ui_begin_frame(int64_t window, int64_t color);
void ail_ui_draw_rect(
    int64_t window,
    int64_t x,
    int64_t y,
    int64_t width,
    int64_t height,
    int64_t color
);
void ail_ui_draw_text(
    int64_t window,
    int64_t x,
    int64_t y,
    int64_t scale,
    int64_t color,
    const char *text
);
void ail_ui_set_clip(
    int64_t window,
    int64_t x,
    int64_t y,
    int64_t width,
    int64_t height
);
void ail_ui_clear_clip(int64_t window);
void ail_ui_end_frame(int64_t window);
void ail_ui_sleep_ms(int64_t ms);
void ail_ui_wait_event_ms(int64_t ms);

#ifdef __cplusplus
}
#endif

#endif
