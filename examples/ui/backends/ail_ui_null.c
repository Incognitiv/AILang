#include "ail_ui_backend.h"

int64_t ail_ui_init(void) { return 1; }
void ail_ui_shutdown(void) {}

int64_t ail_ui_open_window(const char *title, int64_t width, int64_t height) {
    (void)title;
    (void)width;
    (void)height;
    return 1;
}

int64_t ail_ui_open_borderless_window(const char *title, int64_t width, int64_t height) {
    return ail_ui_open_window(title, width, height);
}

void ail_ui_close_window(int64_t window) { (void)window; }
void ail_ui_minimize_window(int64_t window) { (void)window; }
void ail_ui_toggle_maximize_window(int64_t window) { (void)window; }
int64_t ail_ui_window_alive(int64_t window) { (void)window; return 0; }

int64_t ail_ui_platform(void) { return AIL_UI_PLATFORM_UNKNOWN; }
int64_t ail_ui_window_width_px(int64_t window) { (void)window; return 0; }
int64_t ail_ui_window_height_px(int64_t window) { (void)window; return 0; }
int64_t ail_ui_window_scale_milli(int64_t window) { (void)window; return 1000; }
int64_t ail_ui_window_text_scale_milli(int64_t window) { (void)window; return 1000; }
int64_t ail_ui_window_maximized(int64_t window) { (void)window; return 0; }

int64_t ail_ui_poll_event(int64_t window) { (void)window; return AIL_UI_EVENT_NONE; }
int64_t ail_ui_event_x(void) { return 0; }
int64_t ail_ui_event_y(void) { return 0; }
int64_t ail_ui_event_key(void) { return 0; }
int64_t ail_ui_event_width_px(void) { return 0; }
int64_t ail_ui_event_height_px(void) { return 0; }

void ail_ui_begin_frame(int64_t window, int64_t color) {
    (void)window;
    (void)color;
}

void ail_ui_draw_rect(
    int64_t window,
    int64_t x,
    int64_t y,
    int64_t width,
    int64_t height,
    int64_t color
) {
    (void)window;
    (void)x;
    (void)y;
    (void)width;
    (void)height;
    (void)color;
}

void ail_ui_draw_text(
    int64_t window,
    int64_t x,
    int64_t y,
    int64_t scale,
    int64_t color,
    const char *text
) {
    (void)window;
    (void)x;
    (void)y;
    (void)scale;
    (void)color;
    (void)text;
}

void ail_ui_set_clip(int64_t window, int64_t x, int64_t y, int64_t width, int64_t height) {
    (void)window;
    (void)x;
    (void)y;
    (void)width;
    (void)height;
}

void ail_ui_clear_clip(int64_t window) { (void)window; }
void ail_ui_end_frame(int64_t window) { (void)window; }
void ail_ui_sleep_ms(int64_t ms) { (void)ms; }
void ail_ui_wait_event_ms(int64_t ms) { (void)ms; }
