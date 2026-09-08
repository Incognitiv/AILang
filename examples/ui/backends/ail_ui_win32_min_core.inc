#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <windowsx.h>
#include <stdlib.h>
#include <string.h>

#define AIL_DWMWA_WINDOW_CORNER_PREFERENCE 33
#define AIL_DWMWA_BORDER_COLOR 34
#define AIL_DWMWA_USE_HOSTBACKDROPBRUSH 17
#define AIL_DWMWA_SYSTEMBACKDROP_TYPE 38
#define AIL_DWMWCP_DONOTROUND 1
#define AIL_DWMSBT_NONE 1
#define AIL_DWMWA_COLOR_NONE 0xfffffffeu
#define AIL_DWM_BB_ENABLE 0x00000001

typedef HRESULT (WINAPI *AilDwmSetWindowAttributeFn)(HWND hwnd, DWORD attribute, LPCVOID value, DWORD size);
typedef HRESULT (WINAPI *AilDwmExtendFrameIntoClientAreaFn)(HWND hwnd, const void *margins);
typedef HRESULT (WINAPI *AilDwmEnableBlurBehindWindowFn)(HWND hwnd, const void *blur_behind);

typedef struct AilDwmMargins {
    int cxLeftWidth;
    int cxRightWidth;
    int cyTopHeight;
    int cyBottomHeight;
} AilDwmMargins;

typedef struct AilDwmBlurBehind {
    DWORD dwFlags;
    BOOL fEnable;
    HRGN hRgnBlur;
    BOOL fTransitionOnMaximized;
} AilDwmBlurBehind;

typedef struct AilUiWin {
    HWND hwnd;
    int width_px;
    int height_px;
    int scale_milli;
    int alive;
    int borderless;
    int clip_enabled;
    int clip_x0;
    int clip_y0;
    int clip_x1;
    int clip_y1;
    BITMAPINFO bitmap_info;
    HDC memory_dc;
    HBITMAP bitmap;
    HBITMAP old_bitmap;
    uint32_t *pixels;
    uint32_t clear_color;
    int layered_pixels_prepared;
} AilUiWin;

static const char *AIL_UI_CLASS_NAME = "AILangUiWindow";
static int g_class_registered = 0;
static int64_t g_event_x = 0;
static int64_t g_event_y = 0;
static int64_t g_event_key = 0;
static int64_t g_event_width_px = 0;
static int64_t g_event_height_px = 0;
static int64_t g_pending_event = AIL_UI_EVENT_NONE;

static AilUiWin *from_handle(int64_t handle) {
    return (AilUiWin *)(uintptr_t)handle;
}

static void set_pending_event(
    int64_t kind,
    int64_t x,
    int64_t y,
    int64_t key,
    int64_t width_px,
    int64_t height_px
) {
    g_pending_event = kind;
    g_event_x = x;
    g_event_y = y;
    g_event_key = key;
    g_event_width_px = width_px;
    g_event_height_px = height_px;
}

static int query_scale_milli(HWND hwnd) {
    HDC dc = GetDC(hwnd);
    int dpi = dc ? GetDeviceCaps(dc, LOGPIXELSX) : 96;
    if (dc) {
        ReleaseDC(hwnd, dc);
    }
    if (dpi <= 0) {
        dpi = 96;
    }
    return (dpi * 1000) / 96;
}

static void configure_borderless_dwm(HWND hwnd) {
    HMODULE dwm;
    AilDwmSetWindowAttributeFn set_window_attribute;
    AilDwmExtendFrameIntoClientAreaFn extend_frame;
    AilDwmEnableBlurBehindWindowFn enable_blur;
    DWORD corner;
    DWORD border;
    BOOL host_backdrop;
    DWORD backdrop_type;
    AilDwmMargins margins;
    AilDwmBlurBehind blur;

    if (!hwnd) {
        return;
    }

    dwm = LoadLibraryA("dwmapi.dll");
    if (!dwm) {
        return;
    }

    set_window_attribute = (AilDwmSetWindowAttributeFn)GetProcAddress(dwm, "DwmSetWindowAttribute");
    extend_frame = (AilDwmExtendFrameIntoClientAreaFn)GetProcAddress(dwm, "DwmExtendFrameIntoClientArea");
    enable_blur = (AilDwmEnableBlurBehindWindowFn)GetProcAddress(dwm, "DwmEnableBlurBehindWindow");

    if (extend_frame) {
        margins.cxLeftWidth = 0;
        margins.cxRightWidth = 0;
        margins.cyTopHeight = 0;
        margins.cyBottomHeight = 0;
        extend_frame(hwnd, &margins);
    }
    if (enable_blur) {
        memset(&blur, 0, sizeof(blur));
        blur.dwFlags = AIL_DWM_BB_ENABLE;
        blur.fEnable = FALSE;
        enable_blur(hwnd, &blur);
    }
    if (set_window_attribute) {
        corner = AIL_DWMWCP_DONOTROUND;
        border = AIL_DWMWA_COLOR_NONE;
        host_backdrop = FALSE;
        backdrop_type = AIL_DWMSBT_NONE;
        set_window_attribute(hwnd, AIL_DWMWA_WINDOW_CORNER_PREFERENCE, &corner, sizeof(corner));
        set_window_attribute(hwnd, AIL_DWMWA_BORDER_COLOR, &border, sizeof(border));
        set_window_attribute(hwnd, AIL_DWMWA_USE_HOSTBACKDROPBRUSH, &host_backdrop, sizeof(host_backdrop));
        set_window_attribute(hwnd, AIL_DWMWA_SYSTEMBACKDROP_TYPE, &backdrop_type, sizeof(backdrop_type));
    }

    FreeLibrary(dwm);
}

static void release_buffer(AilUiWin *win) {
    if (!win) {
        return;
    }
    if (win->memory_dc) {
        if (win->old_bitmap) {
            SelectObject(win->memory_dc, win->old_bitmap);
        }
        DeleteDC(win->memory_dc);
    }
    if (win->bitmap) {
        DeleteObject(win->bitmap);
    }
    win->memory_dc = NULL;
    win->bitmap = NULL;
    win->old_bitmap = NULL;
    win->pixels = NULL;
    win->layered_pixels_prepared = 0;
    win->width_px = 0;
    win->height_px = 0;
}

static void release_bitmap_resources(HDC memory_dc, HBITMAP bitmap, HBITMAP old_bitmap) {
    if (memory_dc) {
        if (old_bitmap) {
            SelectObject(memory_dc, old_bitmap);
        }
        DeleteDC(memory_dc);
    }
    if (bitmap) {
        DeleteObject(bitmap);
    }
}

static void fill_pixel_buffer(uint32_t *pixels, uint64_t count, uint32_t color) {
    uint64_t i;
    if (!pixels) {
        return;
    }
    for (i = 0; i < count; i++) {
        pixels[i] = color;
    }
}

static int resize_buffer(AilUiWin *win, int width_px, int height_px) {
    uint64_t pixel_count;
    HDC screen_dc;
    BITMAPINFO bitmap_info;
    HDC new_memory_dc;
    HBITMAP new_bitmap;
    HBITMAP new_old_bitmap;
    uint32_t *new_pixels;
    int copy_width;
    int copy_height;
    int y;
    int x;
    if (!win || width_px <= 0 || height_px <= 0) {
        return 0;
    }
    if (win->pixels && win->width_px == width_px && win->height_px == height_px) {
        return 1;
    }

    pixel_count = (uint64_t)width_px * (uint64_t)height_px;
    if (pixel_count > (uint64_t)(1024 * 1024 * 256)) {
        return 0;
    }

    memset(&bitmap_info, 0, sizeof(bitmap_info));
    bitmap_info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bitmap_info.bmiHeader.biWidth = width_px;
    bitmap_info.bmiHeader.biHeight = -height_px;
    bitmap_info.bmiHeader.biPlanes = 1;
    bitmap_info.bmiHeader.biBitCount = 32;
    bitmap_info.bmiHeader.biCompression = BI_RGB;

    new_memory_dc = NULL;
    new_bitmap = NULL;
    new_old_bitmap = NULL;
    new_pixels = NULL;

    screen_dc = GetDC(win->hwnd ? win->hwnd : NULL);
    new_memory_dc = CreateCompatibleDC(screen_dc);
    new_bitmap = CreateDIBSection(
        screen_dc,
        &bitmap_info,
        DIB_RGB_COLORS,
        (void **)&new_pixels,
        NULL,
        0
    );
    if (screen_dc) {
        ReleaseDC(win->hwnd ? win->hwnd : NULL, screen_dc);
    }
    if (!new_memory_dc || !new_bitmap || !new_pixels) {
        release_bitmap_resources(new_memory_dc, new_bitmap, new_old_bitmap);
        return 0;
    }

    new_old_bitmap = (HBITMAP)SelectObject(new_memory_dc, new_bitmap);
    fill_pixel_buffer(new_pixels, pixel_count, win->clear_color & 0x00ffffffu);
    if (win->memory_dc && win->bitmap && win->pixels && win->width_px > 0 && win->height_px > 0) {
        copy_width = win->width_px < width_px ? win->width_px : width_px;
        copy_height = win->height_px < height_px ? win->height_px : height_px;
        for (y = 0; y < copy_height; y++) {
            uint32_t *dst = new_pixels + ((uint64_t)y * (uint64_t)width_px);
            uint32_t *src = win->pixels + ((uint64_t)y * (uint64_t)win->width_px);
            memcpy(dst, src, (size_t)copy_width * sizeof(uint32_t));
            if (copy_width > 0) {
                uint32_t edge = dst[copy_width - 1];
                for (x = copy_width; x < width_px; x++) {
                    dst[x] = edge;
                }
            }
        }
        if (copy_height > 0) {
            uint32_t *src_row = new_pixels + ((uint64_t)(copy_height - 1) * (uint64_t)width_px);
            for (y = copy_height; y < height_px; y++) {
                uint32_t *dst_row = new_pixels + ((uint64_t)y * (uint64_t)width_px);
                memcpy(dst_row, src_row, (size_t)width_px * sizeof(uint32_t));
            }
        }
    }

    release_bitmap_resources(win->memory_dc, win->bitmap, win->old_bitmap);
    win->bitmap_info = bitmap_info;
    win->memory_dc = new_memory_dc;
    win->bitmap = new_bitmap;
    win->old_bitmap = new_old_bitmap;
    win->pixels = new_pixels;
    win->width_px = width_px;
    win->height_px = height_px;
    win->layered_pixels_prepared = 0;
    return 1;
}

static int rounded_top_left_coverage(int px, int py, int radius) {
    const int scale = 8;
    int center = radius * scale;
    int rr = center * center;
    int coverage = 0;
    int sy;
    int sx;

    for (sy = 0; sy < 4; sy++) {
        for (sx = 0; sx < 4; sx++) {
            int sample_x = (px * scale) + (sx * 2) + 1;
            int sample_y = (py * scale) + (sy * 2) + 1;
            int dx = center - sample_x;
            int dy = center - sample_y;
            if ((dx * dx) + (dy * dy) <= rr) {
                coverage++;
            }
        }
    }
    return coverage;
}

static int rounded_top_right_coverage(int px, int py, int radius) {
    const int scale = 8;
    int center = radius * scale;
    int rr = center * center;
    int coverage = 0;
    int sy;
    int sx;

    for (sy = 0; sy < 4; sy++) {
        for (sx = 0; sx < 4; sx++) {
            int sample_x = (px * scale) + (sx * 2) + 1;
            int sample_y = (py * scale) + (sy * 2) + 1;
            int dx = sample_x;
            int dy = center - sample_y;
            if ((dx * dx) + (dy * dy) <= rr) {
                coverage++;
            }
        }
    }
    return coverage;
}

static uint32_t premultiply_argb(uint32_t color, int alpha) {
    uint32_t r = (color >> 16) & 0xffu;
    uint32_t g = (color >> 8) & 0xffu;
    uint32_t b = color & 0xffu;
    if (alpha <= 0) {
        return 0;
    }
    if (alpha >= 255) {
        return 0xff000000u | (color & 0x00ffffffu);
    }
    r = (r * (uint32_t)alpha + 127u) / 255u;
    g = (g * (uint32_t)alpha + 127u) / 255u;
    b = (b * (uint32_t)alpha + 127u) / 255u;
    return ((uint32_t)alpha << 24) | (r << 16) | (g << 8) | b;
}

static int borderless_corner_radius_px(AilUiWin *win) {
    int radius;
    if (!win) {
        return 0;
    }
    radius = (8 * (win->scale_milli > 0 ? win->scale_milli : 1000) + 500) / 1000;
    return radius < 1 ? 1 : radius;
}

static void apply_borderless_window_region(AilUiWin *win) {
    if (!win || !win->hwnd || !win->borderless) {
        return;
    }
    SetWindowRgn(win->hwnd, NULL, TRUE);
}

static void prepare_layered_borderless_pixels(AilUiWin *win) {
    uint64_t count;
    uint64_t i;
    int radius;
    int y;
    int x;

    if (!win || !win->borderless || !win->pixels || win->width_px <= 0 || win->height_px <= 0) {
        return;
    }
    if (win->layered_pixels_prepared) {
        return;
    }

    count = (uint64_t)win->width_px * (uint64_t)win->height_px;
    for (i = 0; i < count; i++) {
        win->pixels[i] = 0xff000000u | (win->pixels[i] & 0x00ffffffu);
    }

    if (IsZoomed(win->hwnd)) {
        return;
    }

    radius = borderless_corner_radius_px(win);
    if (radius <= 0 || radius * 2 > win->width_px || radius > win->height_px) {
        return;
    }

    for (y = 0; y < radius; y++) {
        uint32_t *row = win->pixels + (y * win->width_px);
        for (x = 0; x < radius; x++) {
            int coverage = rounded_top_left_coverage(x, y, radius);
            int alpha = (coverage * 255 + 8) / 16;
            row[x] = premultiply_argb(row[x], alpha);
        }
        for (x = 0; x < radius; x++) {
            int coverage = rounded_top_right_coverage(x, y, radius);
            int alpha = (coverage * 255 + 8) / 16;
            int px = win->width_px - radius + x;
            row[px] = premultiply_argb(row[px], alpha);
        }
    }
    win->layered_pixels_prepared = 1;
}

static void present(AilUiWin *win) {
    HDC dc;
    if (!win || !win->hwnd || !win->pixels || win->width_px <= 0 || win->height_px <= 0) {
        return;
    }
    if (win->borderless && win->memory_dc) {
        RECT rect;
        POINT dst;
        POINT src;
        SIZE size;
        BLENDFUNCTION blend;
        HDC screen_dc;

        prepare_layered_borderless_pixels(win);
        if (GetWindowRect(win->hwnd, &rect)) {
            dst.x = rect.left;
            dst.y = rect.top;
            src.x = 0;
            src.y = 0;
            size.cx = win->width_px;
            size.cy = win->height_px;
            blend.BlendOp = AC_SRC_OVER;
            blend.BlendFlags = 0;
            blend.SourceConstantAlpha = 255;
            blend.AlphaFormat = AC_SRC_ALPHA;
            screen_dc = GetDC(NULL);
            if (screen_dc) {
                if (UpdateLayeredWindow(win->hwnd, screen_dc, &dst, &size, win->memory_dc, &src, 0, &blend, ULW_ALPHA)) {
                    ReleaseDC(NULL, screen_dc);
                    return;
                }
                ReleaseDC(NULL, screen_dc);
            }
        }
    }
    dc = GetDC(win->hwnd);
    if (!dc) {
        return;
    }
    if (win->memory_dc) {
        BitBlt(dc, 0, 0, win->width_px, win->height_px, win->memory_dc, 0, 0, SRCCOPY);
        ReleaseDC(win->hwnd, dc);
        return;
    }

    StretchDIBits(
        dc,
        0,
        0,
        win->width_px,
        win->height_px,
        0,
        0,
        win->width_px,
        win->height_px,
        win->pixels,
        &win->bitmap_info,
        DIB_RGB_COLORS,
        SRCCOPY
    );
    ReleaseDC(win->hwnd, dc);
}

static int is_client_control_area(AilUiWin *win, POINT pt) {
    if (!win) {
        return 0;
    }
    return pt.y < 50 && (pt.x < 560 || pt.x >= win->width_px - 150);
}

static LRESULT CALLBACK ail_ui_wndproc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    AilUiWin *win = (AilUiWin *)GetWindowLongPtrA(hwnd, GWLP_USERDATA);

    if (msg == WM_NCCREATE) {
        CREATESTRUCTA *cs = (CREATESTRUCTA *)lparam;
        win = (AilUiWin *)cs->lpCreateParams;
        SetWindowLongPtrA(hwnd, GWLP_USERDATA, (LONG_PTR)win);
        if (win) {
            win->hwnd = hwnd;
        }
    }

    switch (msg) {
    case WM_GETMINMAXINFO:
        if (win && win->borderless) {
            MINMAXINFO *mmi = (MINMAXINFO *)lparam;
            HMONITOR monitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
            MONITORINFO monitor_info;
            memset(&monitor_info, 0, sizeof(monitor_info));
            monitor_info.cbSize = sizeof(monitor_info);
            if (monitor && GetMonitorInfoA(monitor, &monitor_info)) {
                RECT work = monitor_info.rcWork;
                RECT mon = monitor_info.rcMonitor;
                mmi->ptMaxPosition.x = work.left - mon.left;
                mmi->ptMaxPosition.y = work.top - mon.top;
                mmi->ptMaxSize.x = work.right - work.left;
                mmi->ptMaxSize.y = work.bottom - work.top;
            }
            return 0;
        }
        break;

    case WM_NCCALCSIZE:
        if (win && win->borderless) {
            return 0;
        }
        break;

    case WM_NCPAINT:
        if (win && win->borderless) {
            return 0;
        }
        break;

    case WM_NCACTIVATE:
        if (win && win->borderless) {
            return TRUE;
        }
        break;

    case WM_ERASEBKGND:
        if (win && win->borderless) {
            return 1;
        }
        break;

    case WM_NCHITTEST:
        if (win && win->borderless) {
            POINT pt;
            int edge = 8;
            int radius;
            pt.x = GET_X_LPARAM(lparam);
            pt.y = GET_Y_LPARAM(lparam);
            ScreenToClient(hwnd, &pt);
            radius = borderless_corner_radius_px(win);
            if (!IsZoomed(hwnd) && radius > 0 && pt.y >= 0 && pt.y < radius) {
                if (pt.x >= 0 && pt.x < radius && rounded_top_left_coverage(pt.x, pt.y, radius) == 0) {
                    return HTTRANSPARENT;
                }
                if (
                    pt.x >= win->width_px - radius &&
                    pt.x < win->width_px &&
                    rounded_top_right_coverage(pt.x - (win->width_px - radius), pt.y, radius) == 0
                ) {
                    return HTTRANSPARENT;
                }
            }
            if (pt.y < edge && pt.x < edge) {
                return HTTOPLEFT;
            }
            if (pt.y < edge && pt.x >= win->width_px - edge) {
                return HTTOPRIGHT;
            }
            if (pt.y >= win->height_px - edge && pt.x < edge) {
                return HTBOTTOMLEFT;
            }
            if (pt.y >= win->height_px - edge && pt.x >= win->width_px - edge) {
                return HTBOTTOMRIGHT;
            }
            if (pt.y < edge) {
                return HTTOP;
            }
            if (pt.y >= win->height_px - edge) {
                return HTBOTTOM;
            }
            if (pt.x < edge) {
                return HTLEFT;
            }
            if (pt.x >= win->width_px - edge) {
                return HTRIGHT;
            }
            if (pt.y < 50 && !is_client_control_area(win, pt)) {
                return HTCAPTION;
            }
        }
        break;

    case WM_CLOSE:
        if (win) {
            win->alive = 0;
        }
        set_pending_event(AIL_UI_EVENT_CLOSE, 0, 0, 0, 0, 0);
        DestroyWindow(hwnd);
        return 0;

    case WM_NCDESTROY:
        if (win) {
            win->hwnd = NULL;
            win->alive = 0;
        }
        SetWindowLongPtrA(hwnd, GWLP_USERDATA, 0);
        break;

    case WM_SIZE:
        if (win) {
            int w = LOWORD(lparam);
            int h = HIWORD(lparam);
            if (resize_buffer(win, w, h)) {
                present(win);
            }
            win->scale_milli = query_scale_milli(hwnd);
            apply_borderless_window_region(win);
            set_pending_event(AIL_UI_EVENT_RESIZE, 0, 0, 0, w, h);
        }
        return 0;

    case WM_MOUSEMOVE:
        set_pending_event(
            AIL_UI_EVENT_MOUSE_MOVE,
            GET_X_LPARAM(lparam),
            GET_Y_LPARAM(lparam),
            0,
            0,
            0
        );
        return 0;

    case WM_LBUTTONDOWN:
        SetCapture(hwnd);
        set_pending_event(
            AIL_UI_EVENT_MOUSE_DOWN,
            GET_X_LPARAM(lparam),
            GET_Y_LPARAM(lparam),
            1,
            0,
            0
        );
        return 0;

    case WM_LBUTTONUP:
        ReleaseCapture();
        set_pending_event(
            AIL_UI_EVENT_MOUSE_UP,
            GET_X_LPARAM(lparam),
            GET_Y_LPARAM(lparam),
            1,
            0,
            0
        );
        return 0;

    case WM_KEYDOWN:
        set_pending_event(AIL_UI_EVENT_KEY_DOWN, 0, 0, (int64_t)wparam, 0, 0);
        return 0;

    case WM_KEYUP:
        set_pending_event(AIL_UI_EVENT_KEY_UP, 0, 0, (int64_t)wparam, 0, 0);
        return 0;

    case WM_PAINT:
        if (win) {
            PAINTSTRUCT ps;
            BeginPaint(hwnd, &ps);
            EndPaint(hwnd, &ps);
            present(win);
            return 0;
        }
        break;
    }

    return DefWindowProcA(hwnd, msg, wparam, lparam);
}

static int register_window_class(void) {
    WNDCLASSA wc;
    if (g_class_registered) {
        return 1;
    }
    memset(&wc, 0, sizeof(wc));
    wc.lpfnWndProc = ail_ui_wndproc;
    wc.hInstance = GetModuleHandleA(NULL);
    wc.lpszClassName = AIL_UI_CLASS_NAME;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = NULL;
    if (!RegisterClassA(&wc)) {
        return 0;
    }
    g_class_registered = 1;
    return 1;
}
