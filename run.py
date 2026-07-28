"""Windows 桌面应用入口。

把 Flask 后端跑在后台线程，再用原生窗口（pywebview，Windows 上基于系统自带的
Edge WebView2 内核）展示网页界面，从而成为一个真正的 Windows 桌面应用，
而不是在浏览器里开一个标签页。同时注册全局快捷键、常驻系统托盘。

“点 X 只收起、不退出”的实现（最稳方案，不碰底层窗口过程）：
- 用 pywebview 官方的 closing 事件取消关闭：closing 事件 handler 返回 False 时，
  pywebview 会把 FormClosing 的 args.Cancel 置为 True，从而取消关闭。
- 但 closing 事件默认是**异步**（开新线程）执行的，handler 还没跑完 set() 就返回了，
  导致 Cancel 永远设不上。解决办法：把 closing 事件的 _should_lock 设为 True，
  让它**同步**执行，返回值才可靠。
- handler 里顺便把窗口 win.hide() 收起（托盘仍在），进程永不退。
- 退出只走托盘菜单的“退出”（直接 os._exit，干净结束）。

注意：之前尝试过子类化 WndProc 拦截 WM_CLOSE，但在 WebView2 宿主窗口上极不稳定，
会导致整个窗口崩溃闪退，已弃用。
"""
import os
import sys
import time
import threading
import urllib.request

import app.server as server
from app import config as config_mod
from app import hotkey as hotkey_mod


# ---------------------------------------------------------------------------
# 全局应用状态（模块级，供窗口过程与回调共享）
# ---------------------------------------------------------------------------
APP_STATE = {
    "window": None,
    "hidden": False,
    "last_focused": None,
    "paste_target": None,
}
APP_TITLE = "ClipVault 剪贴板库"
SW_RESTORE = 9


def _log(msg):
    """诊断日志：同时写 data/app.log 并打印到控制台（start.bat 可见）。"""
    try:
        os.makedirs(server.db.DATA_DIR, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}\n"
        with open(os.path.join(server.db.DATA_DIR, "app.log"), "a", encoding="utf-8") as f:
            f.write(line)
        print(line, end="")
    except Exception:  # noqa: BLE001
        pass


def _wait_for_server(port, timeout=10):
    """等 Flask 真正就绪后再打开窗口，避免白屏。"""
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# 单实例：以“端口是否被本程序占用”为真相，并自愈僵死旧进程
# ---------------------------------------------------------------------------
def _port_in_use(port):
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _kill_stale_instances():
    """端口空但 mutex 被占用时，杀掉本项目相关的僵死进程，释放锁。

    精确按进程命令行匹配项目目录 / 同名 exe，并排除自身，避免误伤其他程序。
    """
    try:
        import subprocess

        me = os.getpid()
        ps = (
            f"$me={me}; "
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.ProcessId -ne $me -and $_.CommandLine -and "
            "($_.CommandLine -like '*clipboard-manager*' -or $_.Name -eq 'ClipboardManager.exe') "
            "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            timeout=10, capture_output=True,
        )
        _log("已尝试清理僵死的旧实例进程")
    except Exception as e:  # noqa: BLE001
        _log("清理旧实例失败（可手动在任务管理器结束进程）: " + repr(e))


def _ensure_single_instance():
    """保证同一时间只有一个实例真正运行。

    判定逻辑（以端口为准，避免 ‘僵尸进程持有 mutex’ 导致打不开）：
    1) 端口已被占用 -> 真实例在跑，唤起它并退出。
    2) 端口空、且 mutex 已存在 -> 旧进程僵死(没起 Flask)，清理后自己启动。
    3) 端口空、mutex 也不存在 -> 正常启动。
    """
    if not sys.platform.startswith("win"):
        return True
    if _port_in_use(server.PORT):
        _log("已有实例在运行（端口占用），尝试唤起旧实例")
        if _activate_existing_instance():
            return False
        _log("端口被占用但无法唤起，强制启动新实例")
        return True
    try:
        import win32api
        import winerror
        import win32event

        mutex = win32event.CreateMutex(None, False, "Local\\ClipVaultSingleInstance")
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            _log("检测到僵死旧实例（端口空但锁占用），清理中")
            try:
                win32api.CloseHandle(mutex)
            except Exception:  # noqa: BLE001
                pass
            _kill_stale_instances()
            time.sleep(1.0)  # 给被清理进程一点退出时间，再重新拿锁
            mutex = win32event.CreateMutex(None, False, "Local\\ClipVaultSingleInstance")
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                _log("清理后仍有实例占用，唤起")
                if _activate_existing_instance():
                    return False
        APP_STATE["mutex"] = mutex
        return True
    except Exception as e:  # noqa: BLE001
        _log("单实例锁处理异常（忽略，继续启动）: " + repr(e))
        return True


def _activate_existing_instance():
    try:
        url = f"http://127.0.0.1:{server.PORT}/api/show"
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=1.5):
            pass
        _log("已请求旧实例显示窗口")
        return True
    except Exception as e:  # noqa: BLE001
        _log("请求旧实例显示窗口失败: " + repr(e))
        return False


# ---------------------------------------------------------------------------
# 窗口置顶/聚焦：绕过 Windows 前台锁定
# ---------------------------------------------------------------------------
def _find_hwnd(title):
    """Find this process's window without matching another app with the same title."""
    import win32gui
    import win32process

    current_pid = os.getpid()

    def is_ours(hwnd):
        try:
            return win32process.GetWindowThreadProcessId(hwnd)[1] == current_pid
        except Exception:  # noqa: BLE001
            return False

    try:
        hwnd = win32gui.FindWindow(None, title)
        if hwnd and is_ours(hwnd):
            return hwnd
    except Exception:  # noqa: BLE001
        pass
    found = [0]

    def _cb(hwnd, _lparam):
        try:
            if win32gui.GetWindowText(hwnd) == title and is_ours(hwnd):
                found[0] = hwnd
                return False
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, 0)
    except Exception:  # noqa: BLE001
        pass
    return found[0]


def _force_foreground():
    """把窗口提到最前并聚焦。

    全局热键在后台线程触发，直接 SetForegroundWindow 会被 Windows 前台锁定拦住
    （表现：窗口只在任务栏闪一下、不弹到最前）。这里用三重保险绕过：
    1) AllowSetForegroundWindow(ASFW_ANY) 放开前台限制；
    2) AttachThreadInput 把本线程与当前前台线程绑定后再 SetForegroundWindow；
    3) 关键：用 TOPMOST→NOTOPMOST 调换 Z 序，无论前台锁定与否都能立刻浮到最上层。
    """
    try:
        import win32gui as wg
        import win32process as wp
        import win32con
        import win32api

        hwnd = _find_hwnd(APP_TITLE)
        if not hwnd:
            return
        if wg.IsIconic(hwnd):
            wg.ShowWindow(hwnd, win32con.SW_RESTORE)

        # 1) 放开前台限制
        try:
            win32api.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
        except Exception:  # noqa: BLE001
            pass

        # 2) Bind the thread performing this operation to both input queues.
        # Otherwise the popup can be topmost without owning keyboard focus.
        current_thread = win32api.GetCurrentThreadId()
        target_thread = wp.GetWindowThreadProcessId(hwnd)[0]
        foreground = wg.GetForegroundWindow()
        foreground_thread = (
            wp.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        )
        attached_threads = []
        for other_thread in (foreground_thread, target_thread):
            if (
                other_thread
                and other_thread != current_thread
                and other_thread not in attached_threads
            ):
                wp.AttachThreadInput(current_thread, other_thread, True)
                attached_threads.append(other_thread)
        try:
            wg.BringWindowToTop(hwnd)
            wg.SetForegroundWindow(hwnd)
            wg.SetActiveWindow(hwnd)
        finally:
            for other_thread in reversed(attached_threads):
                wp.AttachThreadInput(current_thread, other_thread, False)

        # 3) 调换 Z 序强制置顶（不受前台锁定影响，确保立刻可见）
        wg.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        wg.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
    except Exception as e:  # noqa: BLE001
        _log("置顶失败: " + repr(e))


def _is_editable_window_class(class_name):
    """Recognize native text controls without guessing from an app title."""
    name = (class_name or "").lower()
    return name == "edit" or name.startswith("richedit") or name.startswith("textbox")


def _is_webview_class(class_name):
    """Identify the keyboard-owning child windows used by Edge WebView2."""
    name = (class_name or "").lower()
    return name.startswith("chrome_widgetwin") or name.startswith("chrome_render")


def _focus_webview_control(window):
    """Focus pywebview's WebView2 control on the WinForms UI thread."""
    try:
        import importlib
        from System import Action

        winforms = importlib.import_module("webview.platforms.winforms")
        instance = winforms.BrowserView.instances.get(window.uid)
        if instance is None:
            return False

        def focus_control():
            instance.browser.webview.Focus()

        instance.Invoke(Action(focus_control))
        return True
    except Exception as e:  # noqa: BLE001
        _log("WebView2 控件聚焦失败: " + repr(e))
        return False


def _focus_webview():
    """Move native keyboard focus into WebView2 after the popup is shown."""
    try:
        import win32api
        import win32gui
        import win32process

        parent = _find_hwnd(APP_TITLE)
        if not parent:
            return False
        children = []

        def collect(child, _):
            if _is_webview_class(win32gui.GetClassName(child)):
                children.append(child)
            return True

        win32gui.EnumChildWindows(parent, collect, 0)
        if not children:
            return False
        # The renderer receives keyboard events; use it when present.
        target = next(
            (child for child in reversed(children)
             if win32gui.GetClassName(child).lower().startswith("chrome_render")),
            children[-1],
        )
        current_thread = win32api.GetCurrentThreadId()
        target_thread = win32process.GetWindowThreadProcessId(target)[0]
        attached = False
        if current_thread != target_thread:
            win32process.AttachThreadInput(current_thread, target_thread, True)
            attached = True
        try:
            win32gui.SetFocus(target)
        finally:
            if attached:
                win32process.AttachThreadInput(current_thread, target_thread, False)
        return True
    except Exception as e:  # noqa: BLE001
        _log("WebView 键盘聚焦失败: " + repr(e))
        return False


def _focus_popup_content(window):
    """Focus the native renderer, then the selectable list after it is ready."""
    _force_foreground()
    if not _focus_webview_control(window):
        _focus_webview()
    try:
        active_element = window.evaluate_js(
            "if(window.focusSelection)focusSelection();"
            "document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : ''"
        )
        if active_element != "list":
            _log("列表聚焦未生效，当前元素: " + repr(active_element))
    except Exception as e:  # noqa: BLE001
        _log("列表聚焦脚本执行失败: " + repr(e))


def _active_input_target(hwnd):
    """Return an editable foreground window only when Windows exposes a caret."""
    if not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import win32gui
        import win32process

        class GUI_THREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        info = GUI_THREADINFO()
        info.cbSize = ctypes.sizeof(info)
        thread_id = win32process.GetWindowThreadProcessId(hwnd)[0]
        if not ctypes.windll.user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return None
        focus = info.hwndFocus or info.hwndCaret
        if focus and _is_editable_window_class(win32gui.GetClassName(focus)):
            return hwnd
        # Chromium and WebView chat clients frequently expose a caret on a
        # renderer HWND rather than a native Edit control.
        if info.hwndCaret:
            return hwnd
    except Exception:  # noqa: BLE001
        return None
    return None


def _show_window(capture_paste_target=False):
    """全局快捷键 / 托盘菜单 / 托盘图标的回调：
    窗口藏起则显示并聚焦；已可见则仅置顶聚焦。"""
    try:
        import win32gui

        if not capture_paste_target:
            APP_STATE["paste_target"] = None

        win = APP_STATE["window"]
        if win is None:
            return
        # 兜底：用 HWND 真实可见性判断，避免 APP_STATE 与 pywebview 内部状态不一致
        hwnd = _find_hwnd(APP_TITLE)
        really_hidden = APP_STATE["hidden"] or (
            hwnd is not None and not win32gui.IsWindowVisible(hwnd)
        )

        if really_hidden:
            _log("触发 -> 显示窗口")
            # 在抢走焦点之前，记录用户当前所在的窗口（粘贴目标）。
            try:
                foreground = win32gui.GetForegroundWindow()
                APP_STATE["last_focused"] = foreground
                if capture_paste_target:
                    # Chat clients built with Chromium/WebView often do not
                    # expose their text field as a native Edit HWND. Keep the
                    # foreground window as the paste destination regardless.
                    APP_STATE["paste_target"] = foreground
                    if not _active_input_target(foreground):
                        _log("未识别原输入控件，仍保留前台窗口作为粘贴目标")
                else:
                    APP_STATE["paste_target"] = None
            except Exception:  # noqa: BLE001
                APP_STATE["last_focused"] = None
                APP_STATE["paste_target"] = None
            # 统一用 HWND 显示（跨线程安全，且能可靠解除 SW_HIDE 的隐藏）
            if hwnd:
                try:
                    win32gui.ShowWindow(hwnd, SW_RESTORE)
                except Exception:  # noqa: BLE001
                    pass
            else:
                try:
                    win.show()
                except Exception:  # noqa: BLE001
                    pass
            APP_STATE["hidden"] = False
        else:
            _log("触发 -> 窗口已可见，仅置顶聚焦")

        _focus_popup_content(win)
        threading.Timer(0.12, _focus_popup_content, args=(win,)).start()
    except Exception as e:  # noqa: BLE001
        _log("回调失败: " + repr(e))


def toggle_window(icon=None, item=None):
    """Open as a normal clipboard browser from the tray or second launch."""
    _show_window(capture_paste_target=False)


def show_from_hotkey():
    """Open from the global shortcut and capture an editable target when present."""
    _show_window(capture_paste_target=True)


def on_closing():
    """closing 事件 handler：取消关闭并把窗口收起（隐藏到托盘）。

    把 closing 事件设为同步执行后，这里返回 False 即可让 pywebview 把
    FormClosing 的 args.Cancel 置为 True，从而取消关闭；同时隐藏窗口。
    """
    try:
        APP_STATE["hidden"] = True
        win = APP_STATE["window"]
        if win is not None:
            win.hide()
            _log("窗口收起（点 X = 隐藏到托盘）")
    except Exception as e:  # noqa: BLE001
        _log("收起失败: " + repr(e))
    return False


def on_quit(icon=None, item=None):
    _log("用户请求退出")
    try:
        if tray_ref["icon"] is not None:
            tray_ref["icon"].stop()
    except Exception:  # noqa: BLE001
        pass
    # 直接结束进程（closing 已取消关闭，destroy 不会真正退出，这里强制结束）
    threading.Timer(0.5, lambda: os._exit(0)).start()


tray_ref = {"icon": None}


def _restore_focus(target, retries=12):
    """把焦点还给粘贴目标窗口（绕过多重 Windows 前台锁定）。

    Windows 的前台锁定是概率性的，单次 SetForegroundWindow 经常被拒。
    这里用「每次重新 AllowSetForegroundWindow + 多次重试 + 每次校验前台是否到位」
    的组合，最大化把焦点还给聊天框的成功率。
    """
    if not target:
        return False
    try:
        import win32gui as wg
        import win32process as wp
        import win32con
        import win32api

        try:
            if not wg.IsWindow(target):
                return False
        except Exception:  # noqa: BLE001
            pass

        our_thread = win32api.GetCurrentThreadId()
        target_thread = wp.GetWindowThreadProcessId(target)[0]
        attached = False
        if our_thread and target_thread and our_thread != target_thread:
            try:
                wp.AttachThreadInput(our_thread, target_thread, True)
                attached = True
            except Exception:  # noqa: BLE001
                pass
        try:
            for _ in range(retries):
                try:
                    win32api.AllowSetForegroundWindow(win32con.ASFW_ANY)
                except Exception:  # noqa: BLE001
                    pass
                # SW_RESTORE turns a maximized window back into a normal one.
                # Only minimized targets need restoring before foregrounding.
                try:
                    if wg.IsIconic(target):
                        wg.ShowWindow(target, win32con.SW_RESTORE)
                except Exception:  # noqa: BLE001
                    pass
                wg.SetForegroundWindow(target)
                # 校验是否真的成为前台窗口
                try:
                    if wg.GetForegroundWindow() == target:
                        return True
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.04)
            # 最后强行再试一次（不再校验）
            try:
                wg.SetForegroundWindow(target)
            except Exception:  # noqa: BLE001
                pass
            try:
                return wg.GetForegroundWindow() == target
            except Exception:  # noqa: BLE001
                return False
        finally:
            if attached:
                try:
                    wp.AttachThreadInput(our_thread, target_thread, False)
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        _log("恢复目标窗口焦点失败: " + repr(e))
        return False


def _send_ctrl_v():
    """向当前前台窗口发送 Ctrl+V（粘贴）。"""
    try:
        import win32api
        import win32con

        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord("V"), 0, 0, 0)
        win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    except Exception as e:  # noqa: BLE001
        _log("发送 Ctrl+V 失败: " + repr(e))


def _send_unicode_text(text):
    """用 SendInput(KEYEVENTF_UNICODE) 把文本当作真实键盘逐字符敲入当前焦点窗口。

    与 Ctrl+V 相比：不依赖系统剪贴板、不要求目标支持粘贴，对微信/QQ/浏览器
    输入框都按真实键盘输入生效。仅在焦点已回到聊天框时作为主路径使用。
    """
    if not text:
        return True
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
            ]

        class _INPUTunion(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("union", _INPUTunion)]

        INPUT_KEYBOARD = 1
        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002

        # 把字符串展开成 UTF-16 扫描码事件（补充平面字符拆成代理对）
        events = []
        for ch in text:
            cp = ord(ch)
            if cp <= 0xFFFF:
                scans = [cp]
            else:
                cp -= 0x10000
                scans = [0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)]
            for sc in scans:
                down = KEYBDINPUT(0, sc, KEYEVENTF_UNICODE, 0, None)
                up = KEYBDINPUT(0, sc, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
                events.append(INPUT(INPUT_KEYBOARD, _INPUTunion(down)))
                events.append(INPUT(INPUT_KEYBOARD, _INPUTunion(up)))

        if not events:
            return True

        arr = (INPUT * len(events))(*events)
        batch = 60
        sent_total = 0
        for i in range(0, len(events), batch):
            chunk = arr[i:i + batch]
            n = user32.SendInput(len(chunk), ctypes.byref(chunk), ctypes.sizeof(INPUT))
            sent_total += n
            if n != len(chunk):
                break
        ok = sent_total == len(events)
        _log("SendInput 输入 %d 个字符，发送事件 %d/%d" % (len(text), sent_total, len(events)))
        return ok
    except Exception as e:  # noqa: BLE001
        _log("Unicode 输入失败: " + repr(e))
        return False


def _hide_our_window():
    """通过句柄隐藏原生窗口（可在非 GUI 线程安全调用）。"""
    try:
        hwnd = _find_hwnd(APP_TITLE)
        if hwnd:
            import win32gui as wg
            import win32con

            wg.ShowWindow(hwnd, win32con.SW_HIDE)
        APP_STATE["hidden"] = True
    except Exception as e:  # noqa: BLE001
        _log("隐藏窗口失败: " + repr(e))


def _paste_target():
    """Use the hotkey target, with the captured foreground as a fallback."""
    return APP_STATE.get("paste_target") or APP_STATE.get("last_focused")


def paste_item(item_id):
    """选中即填入：隐藏本窗口 -> 焦点还给聊天框 -> 把内容送进聊天框。

    文本走两条路径：
      - 焦点成功回到聊天框时，用 SendInput 把文本当作真实键盘“打”进去
        （不污染剪贴板、不要求目标支持粘贴，最稳）；失败时退回剪贴板+Ctrl+V。
      - 焦点没回来时，仍尝试剪贴板+Ctrl+V（部分场景可成）。
    图片只能走剪贴板+Ctrl+V。
    """
    try:
        item = server.db.get_item(item_id)
        if not item:
            _log("填入失败：找不到条目 %s" % item_id)
            return False

        is_text = item["kind"] != "image"
        content = (item.get("content") or "") if is_text else ""

        # Always use the system clipboard and a real Ctrl+V. This keeps rich
        # text, non-Latin characters, and browser/chat inputs on one path.
        if is_text:
            server.clipboard.suppress_next()
            server.clipboard.copy_text_to_clipboard(content)
        else:
            server.clipboard.suppress_next()
            server.clipboard.copy_image_to_clipboard(item["image_path"])

        # 1) 收起本窗口（隐藏后系统会把焦点让给 Z 序下一个窗口）
        _hide_our_window()

        # 2) 焦点还给用户之前所在的窗口（聊天框等），多次重试绕过前台锁定
        target = _paste_target()
        focused = _restore_focus(target)

        # 3) Give the destination time to accept focus, then paste.
        time.sleep(0.08)
        _send_ctrl_v()

        _log("已处理填入条目 %s（focused=%s）" % (item_id, focused))
        return True
    except Exception as e:  # noqa: BLE001
        _log("填入失败: " + repr(e))
        return False


def push_latest_to_obsidian():
    from app import config as config_mod
    from app import obsidian
    from app import notify

    item = server.clipboard.capture_current_item()
    if item:
        _log("Obsidian 推送使用当前系统剪贴板: 条目 %s" % item["id"])
    else:
        item = server.db.get_latest_item()
    if not item:
        notify.notify("ClipVault", "没有可推送的剪贴板记录")
        raise ValueError("没有可推送的剪贴板记录")
    try:
        path = obsidian.export_item(
            item, config_mod.load_config().get("obsidian_dir", "")
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e)[:120]
        notify.notify("ClipVault · 推送失败", msg)
        _log("推送到 Obsidian 失败: " + repr(e))
        raise
    _log("已推送到 Obsidian: " + path)
    try:
        import os

        name = os.path.basename(path)
    except Exception:  # noqa: BLE001
        name = path
    # 系统级通知（窗口隐藏时也能看到）
    notify.notify("ClipVault · 已推送", "已写入 Obsidian：" + (name or path))
    # 作为补充，再走一次托盘气泡
    try:
        tray_ref["icon"].notify("已推送到 Obsidian", "ClipVault")
    except Exception:  # noqa: BLE001
        pass
    return path


def choose_obsidian_folder():
    try:
        import webview
        result = APP_STATE["window"].create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else ""
    except Exception as e:  # noqa: BLE001
        _log("选择 Obsidian 文件夹失败: " + repr(e))
        return ""


def hide_window():
    """供前端 Esc 调用：仅收起窗口，不退出。"""
    _log("窗口收起（Esc / 收起）")
    _hide_our_window()


def main():
    if not _ensure_single_instance():
        return

    try:
        from app import autostart
        if autostart.enable_current_user_startup():
            _log("已启用登录后自动启动")
        else:
            _log("登录后自动启动未启用")
    except Exception as e:  # noqa: BLE001
        _log("设置登录启动失败: " + repr(e))

    _log("初始化数据存储")
    server.db.init_db()
    _log("数据存储已就绪")
    server.set_show_callback(toggle_window)
    server.set_paste_callback(paste_item)
    server.set_hide_callback(hide_window)
    server.set_obsidian_callback(push_latest_to_obsidian)
    server.set_folder_picker_callback(choose_obsidian_folder)

    def hotkey_entries():
        cfg = config_mod.load_config()
        return [
            {
                "name": "main",
                "id": 1,
                "preferred": cfg.get("hotkey") or "ctrl+alt+c",
                "callback": show_from_hotkey,
                "fallbacks": hotkey_mod.FALLBACKS,
            },
            {
                "name": "obsidian",
                "id": 2,
                "preferred": cfg.get("obsidian_hotkey") or "ctrl+alt+o",
                "callback": push_latest_to_obsidian,
                "fallbacks": [],
            },
        ]

    # 运行时重新注册两条热键（在设置里改完快捷键后由后端调用）
    def reload_hotkeys():
        try:
            statuses = hotkey_mod.manager.reload_many(hotkey_entries())
            main_status = statuses.get("main") or {}
            obsidian_status = statuses.get("obsidian") or {}
            _log("热键已重新加载：主=%s，Obsidian=%s" % (
                main_status.get("active") or "不可用",
                obsidian_status.get("active") or "不可用",
            ))
            return statuses
        except Exception as e:  # noqa: BLE001
            _log("热键重载失败: " + repr(e))
            return {}

    server.set_reload_callback(reload_hotkeys)
    server.set_hotkey_status_callback(hotkey_mod.manager.get_statuses)

    _log("启动剪贴板监听与本地服务")
    server.start_monitor()  # 后台监听剪贴板（仅 Windows）
    threading.Thread(target=server.start_flask, daemon=True).start()
    if not _wait_for_server(server.PORT):
        _log("本地服务启动超时")
        return
    _log("本地服务已就绪")

    url = f"http://127.0.0.1:{server.PORT}"
    if os.environ.get("CLIPVAULT_KEYBOARD_DEBUG"):
        url += "?keyboardDebug=1"

    # 允许用 CB_GUI=browser 强制走浏览器模式（用于非 Windows / 调试）
    if sys.platform.startswith("win") and os.environ.get("CB_GUI") != "browser":
        import webview

        # 弹窗风格：无边框、不可缩放，通过拖拽区/自定义关闭按钮操作。
        # 这样看起来更像系统剪贴板弹窗，而不是一个普通应用窗口。
        webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
        debug_port = os.environ.get("CLIPVAULT_DEBUG_PORT")
        if debug_port:
            webview.settings["REMOTE_DEBUGGING_PORT"] = int(debug_port)
        # 系统托盘常驻
        try:
            import pystray
            from PIL import Image, ImageDraw

            def _make_icon():
                img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                d.rounded_rectangle([8, 8, 56, 56], radius=14,
                                    fill=(99, 102, 241, 255))
                d.text((32, 30), "V", fill=(255, 255, 255, 255))
                return img

            menu = pystray.Menu(
                pystray.MenuItem("显示 / 隐藏", toggle_window),
                pystray.MenuItem("退出", on_quit),
            )
            icon = pystray.Icon(
                "ClipVault", _make_icon(), "ClipVault 剪贴板库", menu,
                on_activate=toggle_window,
            )
            tray_ref["icon"] = icon
            threading.Thread(target=icon.run, daemon=True).start()
            _log("托盘已启动")
        except Exception as e:  # noqa: BLE001
            _log("托盘不可用: " + repr(e))

        # 全局热键（被占用会自动尝试备用组合）
        hotkey_mod.set_log(_log)
        statuses = hotkey_mod.manager.register_many(hotkey_entries())
        active = (statuses.get("main") or {}).get("active")
        obsidian_active = (statuses.get("obsidian") or {}).get("active")
        _log("Obsidian 推送快捷键: " + (obsidian_active or "不可用"))
        if active:
            _log("全局热键已生效: " + active)
        else:
            _log("全局热键全部不可用：可用托盘双击 / 菜单「显示」来打开窗口")

        # 创建小弹窗：居中偏上，像系统剪贴板面板
        import win32api as _wapi
        import win32con as _wcon
        sw = _wapi.GetSystemMetrics(_wcon.SM_CXSCREEN)
        sh = _wapi.GetSystemMetrics(_wcon.SM_CYSCREEN)
        width, height = 420, 620
        x = (sw - width) // 2
        y = int((sh - height) * 0.16)
        window = webview.create_window(
            APP_TITLE,
            url,
            width=width,
            height=height,
            x=x,
            y=y,
            background_color="#f3f4f6",
            frameless=True,
            easy_drag=False,
            resizable=False,
        )
        APP_STATE["window"] = window
        APP_STATE["hidden"] = False
        _log("窗口已创建")

        # 关键：把 closing 事件改为**同步**执行，handler 返回 False 才能可靠取消关闭。
        # （pywebview 默认异步执行，会导致 args.Cancel 永远设不上、关闭拦不住）
        try:
            if hasattr(window.events, "closing"):
                window.events.closing._should_lock = True
                window.events.closing += on_closing
                _log("已绑定 closing 事件（点 X = 收起）")
        except Exception as e:  # noqa: BLE001
            _log("closing 事件绑定失败（不影响运行）: " + repr(e))

        webview.start()  # 消息循环一直运行；closing 已取消关闭，进程永不退
    else:
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
