#include "ail_ui_backend.h"

#if defined(_WIN32) || defined(_WIN64)

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

#else

int64_t ail_ui_init(void) { return 0; }
void ail_ui_shutdown(void) {}
int64_t ail_ui_open_window(const char *title, int64_t width, int64_t height) {
    (void)title;
    (void)width;
    (void)height;
    return 0;
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
void ail_ui_begin_frame(int64_t window, int64_t color) { (void)window; (void)color; }
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
void ail_ui_set_clip(
    int64_t window,
    int64_t x,
    int64_t y,
    int64_t width,
    int64_t height
) {
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

#endif
