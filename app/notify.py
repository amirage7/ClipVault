"""系统级通知（Windows 托盘气泡）。

用于全局热键触发的 Obsidian 推送结果提示——这种场景下剪贴板窗口往往处于
隐藏状态，系统级通知比应用内 toast 更可靠、用户一定能看到。

纯 ctypes 实现（Shell_NotifyIcon），不依赖任何第三方包，在冻结后的 exe 中也能用。
"""
import ctypes
import threading
import time
from ctypes import wintypes

# ---- Windows 常量 ----
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_USER = 0x0400
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001
NOTIFYICON_VERSION_4 = 4
IDI_INFORMATION = 32516
HWND_MESSAGE = -3

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HANDLE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeout", wintypes.UINT),  # union uTimeout / uVersion
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_DESTROY:
        try:
            ctypes.windll.user32.PostQuitMessage(0)
        except Exception:  # noqa: BLE001
            pass
        return 0
    return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_WNDPROC = WNDPROC(_wndproc)  # 保持引用，避免被 GC


def _run(title, message, duration):
    try:
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32

        hinst = kernel32.GetModuleHandleW(None)
        cls = "ClipVaultToastCls"
        wc = WNDCLASS()
        wc.hInstance = hinst
        wc.lpszClassName = cls
        wc.lpfnWndProc = _WNDPROC
        wc.style = 0
        try:
            atom = user32.RegisterClassW(ctypes.byref(wc))
        except Exception:  # noqa: BLE001
            atom = user32.GetClassInfoW(hinst, cls, ctypes.byref(wc))
        if not atom:
            return
        hwnd = user32.CreateWindowExW(
            0, cls, "ClipVaultToast", 0, 0, 0, 0, 0, HWND_MESSAGE, None, hinst, None
        )
        if not hwnd:
            return

        hicon = user32.LoadIconW(0, IDI_INFORMATION)
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_USER + 1
        nid.hIcon = hicon
        nid.szTip = "ClipVault"
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

        nid.uVersion = NOTIFYICON_VERSION_4
        shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))

        nid.uFlags = NIF_INFO
        nid.szInfo = message
        nid.szInfoTitle = title
        nid.dwInfoFlags = NIIF_INFO
        nid.uTimeout = 10000
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

        # 维持消息循环，让气泡正常显示，duration 后清理
        start = time.time()
        msg = MSG()
        while time.time() - start < duration + 1:
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1) != 0:
                if msg.message == WM_QUIT:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.05)

        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            user32.DestroyWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def notify(title, message, duration=4):
    """弹出一个短暂的系统通知。任何异常都静默吞掉，绝不影响主流程。"""
    if not hasattr(ctypes, "windll"):
        return
    try:
        t = threading.Thread(target=_run, args=(title, message, duration), daemon=True)
        t.start()
    except Exception:  # noqa: BLE001
        pass
