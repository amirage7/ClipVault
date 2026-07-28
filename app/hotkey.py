"""Windows 全局快捷键（跨进程热键）。

标准做法：创建一个隐藏的 Win32 消息窗口，把 WM_HOTKEY 交给它的 WndProc 处理，
比裸 RegisterHotKey(None) 更可靠（在任意窗口聚焦时都能触发，不受主线程消息循环影响）。

健壮性增强：
- 优先使用用户配置的热键；若被其它程序占用（RegisterHotKey 返回 0 / 1409），
  自动依次尝试备用组合，直到注册成功并记录最终生效的组合。
- 非 Windows 环境下 register() 直接返回 None，由调用方回退。
- 支持修饰键：ctrl / alt / shift / win；主键支持字母、数字、F1~F12、空格等。
- 可通过 reload() 在运行时更换快捷键。
"""
import sys
import threading
import time

if sys.platform.startswith("win"):
    import win32api
    import win32con
    import win32gui

HOTKEY_ID = 1
CLASS_NAME = "ClipVaultHotkeyClass"
_MGR = None  # 指向当前生效的 HotkeyManager，供模块级 _wndproc 访问
_MANAGERS = {}

# 备用热键（按优先级）。用户配置的热键会插在最前面一起尝试。
FALLBACKS = [
    "ctrl+alt+c",
    "ctrl+shift+c",
    "alt+shift+v",
    "win+alt+v",
    "ctrl+win+v",
]

_MODIFIERS = {
    "ctrl": win32con.MOD_CONTROL,
    "alt": win32con.MOD_ALT,
    "shift": win32con.MOD_SHIFT,
    "win": win32con.MOD_WIN,
}


def _vk(key):
    """把主键名解析成 Windows 虚拟键码。"""
    if not sys.platform.startswith("win"):
        return 0
    key = (key or "").lower()
    if key.startswith("f") and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 24:
            return win32con.VK_F1 + n - 1
    if key == "space":
        return win32con.VK_SPACE
    if key == "tab":
        return win32con.VK_TAB
    if key in ("enter", "return"):
        return win32con.VK_RETURN
    if key in ("esc", "escape"):
        return win32con.VK_ESCAPE
    if key == "backspace":
        return win32con.VK_BACK
    if len(key) == 1:
        return win32api.VkKeyScan(key[0]) & 0xFF
    return 0


def parse(hotkey_str):
    """把 'ctrl+alt+v' 解析成 (modifiers, vk)，非法返回 None。"""
    if not hotkey_str:
        return None
    mods = 0
    main = None
    for part in hotkey_str.lower().split("+"):
        part = part.strip()
        if not part:
            continue
        if part in _MODIFIERS:
            mods |= _MODIFIERS[part]
        else:
            main = part
    if main is None:
        return None
    vk = _vk(main)
    if not vk:
        return None
    return (mods, vk)


# 可选诊断日志（由 run.py 注入 _log）。未注入时静默。
_log_fn = None


def set_log(fn):
    global _log_fn
    _log_fn = fn


def _log(msg):
    if _log_fn:
        try:
            _log_fn(msg)
        except Exception:  # noqa: BLE001
            pass


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == win32con.WM_HOTKEY:
        mgr = _MANAGERS.get(hwnd) or globals().get("_MGR")
        callback = mgr.callbacks.get(wparam) if mgr is not None else None
        if callback:
            try:
                callback()
            except Exception as e:  # noqa: BLE001
                _log("热键回调异常: " + repr(e))
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def _register_hotkey(hwnd, cand, parsed, hotkey_id=HOTKEY_ID):
    """Return True when RegisterHotKey succeeds.

    pywin32's RegisterHotKey succeeds by not raising; its return value is not a
    reliable truthy success flag.
    """
    mods, vk = parsed
    try:
        win32gui.RegisterHotKey(hwnd, hotkey_id, mods, vk)
        return True
    except Exception as e:  # noqa: BLE001
        _log("热键注册异常(视为被占用): " + cand + " " + repr(e))
        return False


def _register_window_class(window_class, class_name):
    """Register a message-window class, reusing it after a hotkey reload."""
    try:
        return win32gui.RegisterClass(window_class)
    except Exception as e:  # noqa: BLE001
        if getattr(e, "winerror", None) == 1410:
            return class_name
        raise


class HotkeyManager:
    def __init__(self, hotkey_id=HOTKEY_ID, class_name=CLASS_NAME, fallbacks=None):
        self.hotkey_id = hotkey_id
        self.class_name = class_name
        self.fallbacks = FALLBACKS if fallbacks is None else fallbacks
        self.window = None
        self.callback = None
        self.current = None
        self.active = None  # 最终生效的热键字符串
        self.thread = None
        self._hwnd = None
        self._registered = False
        self.callbacks = {}
        self.statuses = {}
        self._entries = []
        self._registered_ids = []
        self._ready = threading.Event()  # 窗口创建完成后置位，供 register_extra 等待
        self._registered_ready = threading.Event()

    def register(self, window, callback, preferred):
        """注册全局热键：优先 preferred，被占用则尝试 FALLBACKS。返回生效的热键字符串或 None。"""
        if not sys.platform.startswith("win"):
            return None
        self.window = window
        self.callback = callback
        self.callbacks = {self.hotkey_id: callback}
        self.active = None
        self.current = None
        self._registered = False
        self._hwnd = None
        self._ready.clear()
        self._registered_ready.clear()
        globals()["_MGR"] = self

        # 组装候选列表（去重，保留顺序）
        cands = []
        for h in [preferred] + self.fallbacks:
            if h and h not in cands:
                cands.append(h)

        # 若已注册，先注销旧线程
        if self.thread and self.thread.is_alive():
            self.unregister()
            self.thread.join(timeout=1.5)
            time.sleep(0.1)

        self.thread = threading.Thread(target=self._loop, args=(cands,), daemon=True)
        self.thread.start()
        # 等待隐藏窗口真正创建完成（最多 5s），避免 register_extra 在窗口就绪前
        # 读到空 _hwnd 而静默失败（之前“第二热键没反应”的根因）。
        self._ready.wait(timeout=5)
        self._registered_ready.wait(timeout=5)
        return self.active

    def register_many(self, entries):
        """Register multiple shortcuts on one message-window owner thread."""
        if not sys.platform.startswith("win"):
            return {
                entry["name"]: {
                    "requested": entry.get("preferred") or "",
                    "active": "",
                    "ok": False,
                    "error": "当前系统不支持全局快捷键",
                }
                for entry in entries
            }

        if self.thread and self.thread.is_alive():
            self.unregister()
            self.thread.join(timeout=1.5)
            time.sleep(0.1)

        self._entries = [dict(entry) for entry in entries]
        self.callbacks = {
            int(entry["id"]): entry["callback"] for entry in self._entries
        }
        self.statuses = {
            entry["name"]: {
                "requested": entry.get("preferred") or "",
                "active": "",
                "ok": False,
                "error": "等待注册",
            }
            for entry in self._entries
        }
        self.active = None
        self._registered = False
        self._registered_ids = []
        self._hwnd = None
        self._ready.clear()
        self._registered_ready.clear()
        globals()["_MGR"] = self

        self.thread = threading.Thread(
            target=self._loop_many, args=(self._entries,), daemon=True
        )
        self.thread.start()
        self._ready.wait(timeout=5)
        self._registered_ready.wait(timeout=5)
        return {name: dict(status) for name, status in self.statuses.items()}

    def reload_many(self, entries=None):
        return self.register_many(entries or self._entries)

    def get_statuses(self):
        return {name: dict(status) for name, status in self.statuses.items()}

    def _loop_many(self, entries):
        try:
            wc = win32gui.WNDCLASS()
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = self.class_name
            wc.lpfnWndProc = _wndproc
            wc.style = 0
            wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
            wc.hbrBackground = 0
            atom = _register_window_class(wc, self.class_name)
            hwnd = win32gui.CreateWindow(
                atom, self.class_name, 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
            )
        except Exception as e:  # noqa: BLE001
            message = "快捷键窗口创建失败: " + str(e)
            for status in self.statuses.values():
                status["error"] = message
            _log(message)
            self._ready.set()
            self._registered_ready.set()
            return

        self._hwnd = hwnd
        _MANAGERS[hwnd] = self
        self._ready.set()

        for entry in entries:
            name = entry["name"]
            hotkey_id = int(entry["id"])
            preferred = entry.get("preferred") or ""
            fallbacks = entry.get("fallbacks", self.fallbacks)
            candidates = []
            for candidate in [preferred] + list(fallbacks or []):
                if candidate and candidate not in candidates:
                    candidates.append(candidate)

            for candidate in candidates:
                parsed = parse(candidate)
                if not parsed:
                    continue
                if _register_hotkey(hwnd, candidate, parsed, hotkey_id):
                    self.statuses[name] = {
                        "requested": preferred,
                        "active": candidate,
                        "ok": True,
                        "error": "",
                    }
                    self._registered_ids.append(hotkey_id)
                    self._registered = True
                    if name == "main":
                        self.active = candidate
                    _log("已注册%s快捷键: %s" % (name, candidate))
                    break
            else:
                self.statuses[name] = {
                    "requested": preferred,
                    "active": "",
                    "ok": False,
                    "error": "快捷键被占用、无效或注册失败",
                }

        self._registered_ready.set()
        if not self._registered_ids:
            try:
                win32gui.DestroyWindow(hwnd)
            except Exception:  # noqa: BLE001
                pass
            _MANAGERS.pop(hwnd, None)
            self._hwnd = None
            return

        try:
            win32gui.PumpMessages()
        finally:
            hwnd = self._hwnd
            for hotkey_id in self._registered_ids:
                try:
                    win32gui.UnregisterHotKey(hwnd, hotkey_id)
                except Exception:  # noqa: BLE001
                    pass
            try:
                win32gui.DestroyWindow(hwnd)
            except Exception:  # noqa: BLE001
                pass
            _MANAGERS.pop(hwnd, None)
            self._hwnd = None
            self._registered = False
            self._registered_ids = []

    def _loop(self, candidates):
        try:
            wc = win32gui.WNDCLASS()
            wc.hInstance = win32api.GetModuleHandle(None)
            wc.lpszClassName = self.class_name
            wc.lpfnWndProc = _wndproc
            wc.style = 0
            wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
            wc.hbrBackground = 0
            atom = _register_window_class(wc, self.class_name)
            hwnd = win32gui.CreateWindow(
                atom, self.class_name, 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
            )
        except Exception as e:  # noqa: BLE001
            _log("热键隐藏窗口创建失败: " + repr(e))
            self._ready.set()
            self._registered_ready.set()
            return
        self._hwnd = hwnd
        _MANAGERS[hwnd] = self
        self._ready.set()

        for cand in candidates:
            parsed = parse(cand)
            if not parsed:
                _log("热键解析失败，跳过: " + repr(cand))
                continue
            if _register_hotkey(self._hwnd, cand, parsed, self.hotkey_id):
                self.current = parsed
                self.active = cand
                self._registered = True
                _log("已注册全局热键: " + cand)
                break
            else:
                _log("热键被占用，跳过: " + cand)

        if not self._registered:
            _log("所有候选热键均不可用（可能被其它程序全部占用）")
            try:
                win32gui.DestroyWindow(hwnd)
            except Exception:  # noqa: BLE001
                pass
            self._hwnd = None
            self._registered_ready.set()
            return

        self._registered_ready.set()

        try:
            win32gui.PumpMessages()  # 阻塞，直到收到 WM_QUIT
        finally:
            hwnd = self._hwnd
            try:
                win32gui.UnregisterHotKey(hwnd, self.hotkey_id)
            except Exception:  # noqa: BLE001
                pass
            try:
                win32gui.DestroyWindow(hwnd)
            except Exception:  # noqa: BLE001
                pass
            _MANAGERS.pop(hwnd, None)
            self._hwnd = None
            self._registered = False

    def unregister(self):
        """停止消息循环并注销热键（发 WM_QUIT 给隐藏窗口线程）。"""
        if self._hwnd:
            try:
                win32gui.PostMessage(self._hwnd, win32con.WM_QUIT, 0, 0)
            except Exception:  # noqa: BLE001
                pass

    def register_extra(self, hotkey, callback, hotkey_id):
        """Register an additional shortcut on the existing message window."""
        if not self._hwnd:
            # 窗口可能还未就绪，等一下再判断
            self._ready.wait(timeout=5)
        if not self._hwnd:
            _log("第二热键注册失败：热键窗口未就绪")
            return None
        parsed = parse(hotkey)
        if not parsed or not _register_hotkey(self._hwnd, hotkey, parsed, hotkey_id):
            return None
        self.callbacks[hotkey_id] = callback
        return hotkey

    def reload(self, preferred):
        """运行时更换快捷键（优先 preferred + 备用）。返回生效的热键字符串或 None。"""
        if not sys.platform.startswith("win"):
            return None
        w, c = self.window, self.callback
        self.unregister()
        time.sleep(0.2)
        return self.register(w, c, preferred)


# 全局单例，run.py 负责注册，server.py 的接口负责热重载。
manager = HotkeyManager()
