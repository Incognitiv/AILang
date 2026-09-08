int64_t ail_ui_init(void) {
    SetProcessDPIAware();
    return register_window_class() ? 1 : 0;
}

void ail_ui_shutdown(void) {
}

int64_t ail_ui_platform(void) {
    return AIL_UI_PLATFORM_WINDOWS;
}

int64_t ail_ui_open_window(const char *title, int64_t width, int64_t height) {
    AilUiWin *win;
    RECT rect;
    DWORD style = WS_OVERLAPPEDWINDOW | WS_VISIBLE;

    if (!register_window_class()) {
        return 0;
    }

    win = (AilUiWin *)calloc(1, sizeof(AilUiWin));
    if (!win) {
        return 0;
    }

    win->alive = 1;
    win->clear_color = 0xf5f7fb;
    rect.left = 0;
    rect.top = 0;
    rect.right = (LONG)width;
    rect.bottom = (LONG)height;
    AdjustWindowRect(&rect, style, FALSE);

    win->hwnd = CreateWindowExA(
        0,
        AIL_UI_CLASS_NAME,
        title ? title : "AILang UI",
        style,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        rect.right - rect.left,
        rect.bottom - rect.top,
        NULL,
        NULL,
        GetModuleHandleA(NULL),
        win
    );
    if (!win->hwnd) {
        free(win);
        return 0;
    }

    configure_borderless_dwm(win->hwnd);
    SetWindowPos(
        win->hwnd,
        NULL,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
    );
    win->scale_milli = query_scale_milli(win->hwnd);
    resize_buffer(win, (int)width, (int)height);
    apply_borderless_window_region(win);
    ShowWindow(win->hwnd, SW_SHOW);
    UpdateWindow(win->hwnd);
    return (int64_t)(uintptr_t)win;
}

int64_t ail_ui_open_borderless_window(const char *title, int64_t width, int64_t height) {
    AilUiWin *win;
    RECT rect;
    DWORD style = WS_POPUP | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU | WS_VISIBLE;

    if (!register_window_class()) {
        return 0;
    }

    win = (AilUiWin *)calloc(1, sizeof(AilUiWin));
    if (!win) {
        return 0;
    }

    win->alive = 1;
    win->borderless = 1;
    win->clear_color = 0xf5f7fb;
    rect.left = 0;
    rect.top = 0;
    rect.right = (LONG)width;
    rect.bottom = (LONG)height;

    win->hwnd = CreateWindowExA(
        WS_EX_LAYERED,
        AIL_UI_CLASS_NAME,
        title ? title : "AILang UI",
        style,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        rect.right - rect.left,
        rect.bottom - rect.top,
        NULL,
        NULL,
        GetModuleHandleA(NULL),
        win
    );
    if (!win->hwnd) {
        free(win);
        return 0;
    }

    win->scale_milli = query_scale_milli(win->hwnd);
    resize_buffer(win, (int)width, (int)height);
    apply_borderless_window_region(win);
    ShowWindow(win->hwnd, SW_SHOW);
    UpdateWindow(win->hwnd);
    return (int64_t)(uintptr_t)win;
}

void ail_ui_close_window(int64_t window) {
    AilUiWin *win = from_handle(window);
    if (!win) {
        return;
    }
    if (win->hwnd && IsWindow(win->hwnd)) {
        DestroyWindow(win->hwnd);
    }
    release_buffer(win);
    free(win);
}

void ail_ui_minimize_window(int64_t window) {
    AilUiWin *win = from_handle(window);
    if (!win || !win->hwnd || !IsWindow(win->hwnd)) {
        return;
    }
    ShowWindow(win->hwnd, SW_MINIMIZE);
}

void ail_ui_toggle_maximize_window(int64_t window) {
    AilUiWin *win = from_handle(window);
    if (!win || !win->hwnd || !IsWindow(win->hwnd)) {
        return;
    }
    ShowWindow(win->hwnd, IsZoomed(win->hwnd) ? SW_RESTORE : SW_MAXIMIZE);
}

int64_t ail_ui_window_alive(int64_t window) {
    AilUiWin *win = from_handle(window);
    return (win && win->alive) ? 1 : 0;
}

int64_t ail_ui_window_width_px(int64_t window) {
    AilUiWin *win = from_handle(window);
    return win ? win->width_px : 0;
}

int64_t ail_ui_window_height_px(int64_t window) {
    AilUiWin *win = from_handle(window);
    return win ? win->height_px : 0;
}

int64_t ail_ui_window_scale_milli(int64_t window) {
    AilUiWin *win = from_handle(window);
    return win ? win->scale_milli : 1000;
}

int64_t ail_ui_window_text_scale_milli(int64_t window) {
    return ail_ui_window_scale_milli(window);
}

int64_t ail_ui_window_maximized(int64_t window) {
    AilUiWin *win = from_handle(window);
    return (win && win->hwnd && IsZoomed(win->hwnd)) ? 1 : 0;
}

int64_t ail_ui_poll_event(int64_t window) {
    AilUiWin *win = from_handle(window);
    MSG msg;
    int64_t event_kind;

    if (g_pending_event != AIL_UI_EVENT_NONE) {
        event_kind = g_pending_event;
        g_pending_event = AIL_UI_EVENT_NONE;
        return event_kind;
    }

    while (PeekMessageA(&msg, NULL, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
        if (g_pending_event != AIL_UI_EVENT_NONE) {
            break;
        }
    }

    if (win && !IsWindow(win->hwnd)) {
        win->alive = 0;
    }

    event_kind = g_pending_event;
    g_pending_event = AIL_UI_EVENT_NONE;
    return event_kind;
}

int64_t ail_ui_event_x(void) {
    return g_event_x;
}

int64_t ail_ui_event_y(void) {
    return g_event_y;
}

int64_t ail_ui_event_key(void) {
    return g_event_key;
}

int64_t ail_ui_event_width_px(void) {
    return g_event_width_px;
}

int64_t ail_ui_event_height_px(void) {
    return g_event_height_px;
}

static void fill_rect(AilUiWin *win, int64_t x, int64_t y, int64_t width, int64_t height, int64_t color) {
    uint32_t c = (uint32_t)(color & 0x00ffffff);
    int64_t x0 = x < 0 ? 0 : x;
    int64_t y0 = y < 0 ? 0 : y;
    int64_t x1 = x + width;
    int64_t y1 = y + height;
    int64_t yy;
    int64_t xx;

    if (!win || !win->pixels || width <= 0 || height <= 0) {
        return;
    }
    win->layered_pixels_prepared = 0;
    if (x1 > win->width_px) {
        x1 = win->width_px;
    }
    if (y1 > win->height_px) {
        y1 = win->height_px;
    }
    if (win->clip_enabled) {
        if (x0 < win->clip_x0) {
            x0 = win->clip_x0;
        }
        if (y0 < win->clip_y0) {
            y0 = win->clip_y0;
        }
        if (x1 > win->clip_x1) {
            x1 = win->clip_x1;
        }
        if (y1 > win->clip_y1) {
            y1 = win->clip_y1;
        }
    }
    if (x0 >= x1 || y0 >= y1) {
        return;
    }

    for (yy = y0; yy < y1; yy++) {
        uint32_t *row = win->pixels + (yy * win->width_px);
        for (xx = x0; xx < x1; xx++) {
            row[xx] = c;
        }
    }
}

#define AIL_UI_GLYPH(ch, r0, r1, r2, r3, r4, r5, r6) \
    case ch: { \
        static const uint8_t rows[7] = {r0, r1, r2, r3, r4, r5, r6}; \
        return rows[row]; \
    }

static uint8_t glyph_row(char ch, int row) {
    if (row < 0 || row >= 7) {
        return 0;
    }
    if (ch >= 'a' && ch <= 'z') {
        ch = (char)(ch - ('a' - 'A'));
    }
    switch (ch) {
    AIL_UI_GLYPH('A', 0x0e, 0x11, 0x11, 0x1f, 0x11, 0x11, 0x11)
    AIL_UI_GLYPH('B', 0x1e, 0x11, 0x11, 0x1e, 0x11, 0x11, 0x1e)
    AIL_UI_GLYPH('C', 0x0e, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0e)
    AIL_UI_GLYPH('D', 0x1e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1e)
    AIL_UI_GLYPH('E', 0x1f, 0x10, 0x10, 0x1e, 0x10, 0x10, 0x1f)
    AIL_UI_GLYPH('F', 0x1f, 0x10, 0x10, 0x1e, 0x10, 0x10, 0x10)
    AIL_UI_GLYPH('G', 0x0e, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0f)
    AIL_UI_GLYPH('H', 0x11, 0x11, 0x11, 0x1f, 0x11, 0x11, 0x11)
    AIL_UI_GLYPH('I', 0x1f, 0x04, 0x04, 0x04, 0x04, 0x04, 0x1f)
    AIL_UI_GLYPH('J', 0x07, 0x02, 0x02, 0x02, 0x12, 0x12, 0x0c)
    AIL_UI_GLYPH('K', 0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11)
    AIL_UI_GLYPH('L', 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1f)
    AIL_UI_GLYPH('M', 0x11, 0x1b, 0x15, 0x15, 0x11, 0x11, 0x11)
    AIL_UI_GLYPH('N', 0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11)
    AIL_UI_GLYPH('O', 0x0e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e)
    AIL_UI_GLYPH('P', 0x1e, 0x11, 0x11, 0x1e, 0x10, 0x10, 0x10)
    AIL_UI_GLYPH('Q', 0x0e, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0d)
    AIL_UI_GLYPH('R', 0x1e, 0x11, 0x11, 0x1e, 0x14, 0x12, 0x11)
    AIL_UI_GLYPH('S', 0x0f, 0x10, 0x10, 0x0e, 0x01, 0x01, 0x1e)
    AIL_UI_GLYPH('T', 0x1f, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04)
    AIL_UI_GLYPH('U', 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e)
    AIL_UI_GLYPH('V', 0x11, 0x11, 0x11, 0x11, 0x11, 0x0a, 0x04)
    AIL_UI_GLYPH('W', 0x11, 0x11, 0x11, 0x15, 0x15, 0x1b, 0x11)
    AIL_UI_GLYPH('X', 0x11, 0x0a, 0x04, 0x04, 0x04, 0x0a, 0x11)
    AIL_UI_GLYPH('Y', 0x11, 0x0a, 0x04, 0x04, 0x04, 0x04, 0x04)
    AIL_UI_GLYPH('Z', 0x1f, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1f)
    AIL_UI_GLYPH('0', 0x0e, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0e)
    AIL_UI_GLYPH('1', 0x04, 0x0c, 0x04, 0x04, 0x04, 0x04, 0x0e)
    AIL_UI_GLYPH('2', 0x0e, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1f)
    AIL_UI_GLYPH('3', 0x1e, 0x01, 0x01, 0x0e, 0x01, 0x01, 0x1e)
    AIL_UI_GLYPH('4', 0x02, 0x06, 0x0a, 0x12, 0x1f, 0x02, 0x02)
    AIL_UI_GLYPH('5', 0x1f, 0x10, 0x10, 0x1e, 0x01, 0x01, 0x1e)
    AIL_UI_GLYPH('6', 0x0e, 0x10, 0x10, 0x1e, 0x11, 0x11, 0x0e)
    AIL_UI_GLYPH('7', 0x1f, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08)
    AIL_UI_GLYPH('8', 0x0e, 0x11, 0x11, 0x0e, 0x11, 0x11, 0x0e)
    AIL_UI_GLYPH('9', 0x0e, 0x11, 0x11, 0x0f, 0x01, 0x01, 0x0e)
    AIL_UI_GLYPH('.', 0x00, 0x00, 0x00, 0x00, 0x00, 0x0c, 0x0c)
    AIL_UI_GLYPH(':', 0x00, 0x0c, 0x0c, 0x00, 0x0c, 0x0c, 0x00)
    AIL_UI_GLYPH('-', 0x00, 0x00, 0x00, 0x1f, 0x00, 0x00, 0x00)
    AIL_UI_GLYPH('_', 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1f)
    AIL_UI_GLYPH('/', 0x01, 0x01, 0x02, 0x04, 0x08, 0x10, 0x10)
    AIL_UI_GLYPH('+', 0x00, 0x04, 0x04, 0x1f, 0x04, 0x04, 0x00)
    default:
        return 0;
    }
}

#undef AIL_UI_GLYPH

void ail_ui_begin_frame(int64_t window, int64_t color) {
    AilUiWin *win = from_handle(window);
    uint32_t c = (uint32_t)(color & 0x00ffffff);
    uint64_t count;
    if (!win || !win->pixels) {
        return;
    }
    win->clear_color = c;
    win->layered_pixels_prepared = 0;
    win->clip_enabled = 0;
    count = (uint64_t)win->width_px * (uint64_t)win->height_px;
    fill_pixel_buffer(win->pixels, count, c);
}

void ail_ui_draw_rect(
    int64_t window,
    int64_t x,
    int64_t y,
    int64_t width,
    int64_t height,
    int64_t color
) {
    fill_rect(from_handle(window), x, y, width, height, color);
}

void ail_ui_draw_text(
    int64_t window,
    int64_t x,
    int64_t y,
    int64_t scale,
    int64_t color,
    const char *text
) {
    AilUiWin *win = from_handle(window);
    HFONT font;
    HFONT old_font;
    int height;
    int r;
    int g;
    int b;

    if (!win || !win->memory_dc || !text) {
        return;
    }
    win->layered_pixels_prepared = 0;
    if (scale <= 0) {
        scale = 1;
    }
    height = 12 + ((int)scale - 1) * 4;
    if (height < 10) {
        height = 10;
    }
    if (height > 30) {
        height = 30;
    }

    font = CreateFontA(
        -height,
        0,
        0,
        0,
        FW_NORMAL,
        FALSE,
        FALSE,
        FALSE,
        DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS,
        CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY,
        DEFAULT_PITCH | FF_DONTCARE,
        "Segoe UI"
    );
    if (!font) {
        return;
    }

    SaveDC(win->memory_dc);
    if (win->clip_enabled) {
        IntersectClipRect(win->memory_dc, win->clip_x0, win->clip_y0, win->clip_x1, win->clip_y1);
    }
    old_font = (HFONT)SelectObject(win->memory_dc, font);
    SetBkMode(win->memory_dc, TRANSPARENT);
    r = (int)((color >> 16) & 0xff);
    g = (int)((color >> 8) & 0xff);
    b = (int)(color & 0xff);
    SetTextColor(win->memory_dc, RGB(r, g, b));
    TextOutA(win->memory_dc, (int)x, (int)y, text, lstrlenA(text));
    SelectObject(win->memory_dc, old_font);
    RestoreDC(win->memory_dc, -1);
    DeleteObject(font);
}

void ail_ui_set_clip(int64_t window, int64_t x, int64_t y, int64_t width, int64_t height) {
    AilUiWin *win = from_handle(window);
    int64_t x0 = x < 0 ? 0 : x;
    int64_t y0 = y < 0 ? 0 : y;
    int64_t x1 = x + width;
    int64_t y1 = y + height;

    if (!win || width <= 0 || height <= 0) {
        return;
    }
    if (x1 > win->width_px) {
        x1 = win->width_px;
    }
    if (y1 > win->height_px) {
        y1 = win->height_px;
    }
    if (x0 >= x1 || y0 >= y1) {
        win->clip_enabled = 1;
        win->clip_x0 = 0;
        win->clip_y0 = 0;
        win->clip_x1 = 0;
        win->clip_y1 = 0;
        return;
    }

    win->clip_enabled = 1;
    win->clip_x0 = (int)x0;
    win->clip_y0 = (int)y0;
    win->clip_x1 = (int)x1;
    win->clip_y1 = (int)y1;
}

void ail_ui_clear_clip(int64_t window) {
    AilUiWin *win = from_handle(window);
    if (win) {
        win->clip_enabled = 0;
    }
}

void ail_ui_end_frame(int64_t window) {
    present(from_handle(window));
}

void ail_ui_sleep_ms(int64_t ms) {
    if (ms <= 0) {
        return;
    }
    Sleep((DWORD)ms);
}

void ail_ui_wait_event_ms(int64_t ms) {
    DWORD timeout = ms < 0 ? INFINITE : (DWORD)ms;
    MsgWaitForMultipleObjects(0, NULL, FALSE, timeout, QS_ALLINPUT);
}
