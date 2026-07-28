"""Windows 剪贴板监听、自动分类与回写。

分类规则：
  - 剪贴板当前为图片  -> image（保存 PNG 原图 + 缩略图）
  - 纯文本且整体是 URL -> url
  - 其它纯文本        -> text

win32clipboard / PIL 仅在函数内惰性导入，便于在非 Windows 环境下也能导入本模块做测试。
"""
import io
import os
import re
import time
import hashlib

from . import db
from . import config

KIND_TEXT = "text"
KIND_URL = "url"
KIND_IMAGE = "image"

URL_RE = re.compile(r"^\s*(https?://|www\.)[^\s]+\s*$", re.IGNORECASE)
SENSITIVE_RE = re.compile(
    r"(?i)(?:\b(?:password|passwd|pwd|api[ _-]?key|access[ _-]?token|secret)\b"
    r"\s*[:=：]\s*\S{4,}|"
    r"(?:密码|验证码|校验码|动态码)\s*(?:[:=：]|是|为)\s*\S{4,}|"
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b)"
)

# 常见进程名 -> 友好中文名（复制来源展示用）
_APP_NAMES = {
    "wechat.exe": "微信",
    "weixin.exe": "微信",
    "wechatappex.exe": "微信",
    "qq.exe": "QQ",
    "wxwork.exe": "企业微信",
    "dingtalk.exe": "钉钉",
    "feishu.exe": "飞书",
    "bytedancefeishu.exe": "飞书",
    "notepad.exe": "记事本",
    "notepad++.exe": "Notepad++",
    "code.exe": "VS Code",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "explorer.exe": "文件资源管理器",
    "typora.exe": "Typora",
    "excel.exe": "Excel",
    "winword.exe": "Word",
    "powerpnt.exe": "PowerPoint",
    "devenv.exe": "Visual Studio",
    "outlook.exe": "Outlook",
    "powershell.exe": "PowerShell",
    "cmd.exe": "命令提示符",
    "windowsterminal.exe": "终端",
    "wps.exe": "WPS",
    "wpp.exe": "WPS 演示",
    "et.exe": "WPS 表格",
    "wword.exe": "WPS 文字",
    "foxmail.exe": "Foxmail",
    "slack.exe": "Slack",
    "telegram.exe": "Telegram",
    "discord.exe": "Discord",
    "skype.exe": "Skype",
    "idea64.exe": "IntelliJ IDEA",
    "pycharm64.exe": "PyCharm",
    "sublime_text.exe": "Sublime Text",
    "eclipse.exe": "Eclipse",
    "postman.exe": "Postman",
    "xmind.exe": "XMind",
    "youdao.exe": "有道词典",
    "doubao.exe": "豆包",
    "kimi.exe": "Kimi",
}


def _friendly_app_name(exe_path):
    """把进程路径转成展示名：已知映射优先，否则用 exe 名（去 .exe）。"""
    if not exe_path:
        return None
    name = os.path.basename(exe_path)
    key = name.lower()
    if key in _APP_NAMES:
        return _APP_NAMES[key]
    return name[:-4] if key.endswith(".exe") else name


def get_active_app():
    """返回 (友好应用名, 窗口标题)。复制发生时调用，记录“从哪个软件复制的”。"""
    try:
        import win32gui
        import win32process
        import win32api
        import win32con

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None, None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        title = win32gui.GetWindowText(hwnd)
        exe = None
        try:
            hproc = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                False, pid,
            )
            try:
                exe = win32process.GetModuleFileNameEx(hproc, 0)
            finally:
                win32api.CloseHandle(hproc)
        except Exception:  # noqa: BLE001
            exe = None
        return _friendly_app_name(exe), (title or None)
    except Exception:  # noqa: BLE001
        return None, None


_last_sig = [None]

# 粘贴功能会先把内容写回剪贴板再模拟 Ctrl+V。为避免“我们刚粘贴出去的内容”
# 又被监听线程当成一次新的复制而重复入库，这里用一次性抑制标志跳过下一次变化。
_suppress_next = [False]


def suppress_next():
    """请求剪贴板监听在检测到下一次变化时跳过（用于粘贴后不重复记录）。"""
    _suppress_next[0] = True


def _read_clipboard():
    """读取当前剪贴板内容，返回 (kind, payload) 或 None。"""
    from PIL import ImageGrab
    import win32clipboard
    import win32con

    # 1) 图片优先
    try:
        img = ImageGrab.grabclipboard()
    except Exception:
        img = None
    if hasattr(img, "save"):  # PIL.Image.Image
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return (KIND_IMAGE, buf.getvalue())

    # 2) 文本
    try:
        win32clipboard.OpenClipboard()
        try:
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except Exception:
            text = None
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        text = None

    if text:
        text = text.strip()
        if URL_RE.match(text):
            return (KIND_URL, text)
        return (KIND_TEXT, text)
    return None


def should_store(kind, payload, app_name, settings=None):
    settings = settings or config.load_config()
    if settings.get("monitor_paused"):
        return False
    excluded = {str(name).strip().casefold() for name in settings.get("excluded_apps", [])}
    if app_name and app_name.strip().casefold() in excluded:
        return False
    if kind != KIND_IMAGE and settings.get("sensitive_filter", True):
        if SENSITIVE_RE.search(str(payload or "")):
            return False
    return True


def _store_clipboard_data(data, app_info=None):
    """Persist one clipboard payload and return the complete saved item."""
    kind, payload = data
    app_name, app_title = app_info or get_active_app()

    if kind == KIND_IMAGE:
        from PIL import Image

        img = Image.open(io.BytesIO(payload)).convert("RGB")
        normalized = io.BytesIO()
        img.save(normalized, "PNG")
        image_bytes = normalized.getvalue()
        item_id, created = db.upsert_item(
            kind,
            content_hash=db.content_signature(kind, image_bytes),
            source_app=app_name,
            source_title=app_title,
        )
        if not created:
            return db.get_item(item_id)
        path = os.path.join(db.IMG_DIR, f"{item_id}.png")
        thumb = os.path.join(db.IMG_DIR, f"{item_id}_t.png")
        with open(path, "wb") as image_file:
            image_file.write(image_bytes)
        img.thumbnail((360, 360))
        img.save(thumb, "PNG")
        db.update_paths(item_id, path, thumb)
    else:
        item_id, _created = db.upsert_item(
            kind,
            content=payload,
            content_hash=db.content_signature(kind, payload),
            source_app=app_name,
            source_title=app_title,
        )
    return db.get_item(item_id)


def capture_current_item():
    """Capture the current system clipboard immediately for an explicit action."""
    data = _read_clipboard()
    if not data:
        return None
    kind, payload = data
    raw = payload if isinstance(payload, bytes) else payload.encode("utf-8", "ignore")
    _last_sig[0] = kind + ":" + hashlib.md5(raw).hexdigest()
    return _store_clipboard_data(data)


def poll_once():
    data = _read_clipboard()
    if not data:
        return
    kind, payload = data
    raw = payload if isinstance(payload, bytes) else payload.encode("utf-8", "ignore")
    sig = kind + ":" + hashlib.md5(raw).hexdigest()
    if sig == _last_sig[0]:
        return
    _last_sig[0] = sig

    # 跳过“粘贴功能”注入的那一次剪贴板变化（不入库），但同步最新签名，
    # 以免影响后续真实复制的去重判断。
    if _suppress_next[0]:
        _suppress_next[0] = False
        return

    app_info = get_active_app()
    if not should_store(kind, payload, app_info[0]):
        return
    _store_clipboard_data(data, app_info=app_info)


def monitor_loop(interval=0.5):
    last_cleanup = 0.0
    while True:
        try:
            poll_once()
            now = time.time()
            if now - last_cleanup >= 86400:
                settings = config.load_config()
                db.cleanup_old_items(settings.get("retention_days", 0))
                last_cleanup = now
        except Exception:
            pass
        time.sleep(interval)


def copy_text_to_clipboard(text):
    import win32clipboard
    import win32con

    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    win32clipboard.CloseClipboard()


def copy_image_to_clipboard(path):
    from PIL import Image
    import win32clipboard
    import win32con

    img = Image.open(path).convert("RGB")
    output = io.BytesIO()
    img.save(output, "BMP")
    data = output.getvalue()[14:]  # 去掉 BMP 文件头，仅保留 DIB 数据
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32con.CF_DIB, data)
    win32clipboard.CloseClipboard()
