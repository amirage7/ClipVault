"""Flask 服务：提供静态前端 + 剪贴板历史 REST 接口，并启动后台剪贴板监听。"""
import os
import sys
import threading
import time
import webbrowser

from flask import (
    Flask,
    request,
    jsonify,
    send_file,
    send_from_directory,
    abort,
)

from . import db
from . import clipboard
from . import config
from . import hotkey as hotkey_mod
from . import autostart
from . import classifier

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
PORT = int(os.environ.get("CB_PORT", "5183"))

app = Flask(__name__, static_folder=STATIC)
show_callback = None
paste_callback = None
hide_callback = None
obsidian_callback = None
folder_picker_callback = None
hotkey_status_callback = None


def set_show_callback(callback):
    global show_callback
    show_callback = callback


def set_paste_callback(callback):
    global paste_callback
    paste_callback = callback


def set_hide_callback(callback):
    global hide_callback
    hide_callback = callback


def set_obsidian_callback(callback):
    global obsidian_callback
    obsidian_callback = callback


def set_folder_picker_callback(callback):
    global folder_picker_callback
    folder_picker_callback = callback


def set_hotkey_status_callback(callback):
    global hotkey_status_callback
    hotkey_status_callback = callback


reload_callback = None


def set_reload_callback(callback):
    global reload_callback
    reload_callback = callback


@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.route("/api/items")
def api_items():
    kind = request.args.get("kind", "")
    q = request.args.get("q", "")
    try:
        limit = int(request.args.get("limit", "40"))
    except ValueError:
        limit = 40
    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        offset = 0
    fav = request.args.get("fav")
    days = request.args.get("days")
    try:
        days = int(days) if days else None
    except ValueError:
        days = None
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()
    source = request.args.get("source", "").strip()
    smart_category = request.args.get("smart_category", "").strip()
    if smart_category not in classifier.CATEGORY_LABELS:
        smart_category = ""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    filters = {
        "kind": kind,
        "q": q,
        "fav": (fav == "1"),
        "days": days,
        "start_date": start_date or None,
        "end_date": end_date or None,
        "source": source or None,
        "smart_category": smart_category or None,
    }
    items = db.list_items(limit=limit, offset=offset, **filters)
    total = db.count_items(**filters)
    return jsonify({
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(items) < total,
    })


@app.route("/api/items", methods=["DELETE"])
def api_clear_items():
    deleted = db.clear_items()
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats())


@app.route("/api/show", methods=["POST"])
def api_show():
    if show_callback is None:
        return jsonify({"ok": False, "error": "show callback unavailable"}), 503
    show_callback()
    return jsonify({"ok": True})


@app.route("/api/paste/<int:item_id>", methods=["POST"])
def api_paste(item_id):
    """选中即粘贴：把指定条目内容写回剪贴板并模拟 Ctrl+V 到之前聚焦的窗口。"""
    if paste_callback is None:
        return jsonify({"ok": False, "error": "paste unavailable"}), 503
    ok = paste_callback(item_id)
    return jsonify({"ok": bool(ok)})


@app.route("/api/hide", methods=["POST"])
def api_hide():
    """收起窗口（仅隐藏到托盘，不退出）。"""
    if hide_callback is None:
        return jsonify({"ok": False}), 503
    hide_callback()
    return jsonify({"ok": True})


@app.route("/api/obsidian/push", methods=["POST"])
def api_obsidian_push():
    if obsidian_callback is None:
        return jsonify({"ok": False, "error": "obsidian unavailable"}), 503
    try:
        return jsonify({"ok": True, "path": obsidian_callback()})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/obsidian/folder", methods=["POST"])
def api_obsidian_folder():
    if folder_picker_callback is None:
        return jsonify({"ok": False, "error": "folder picker unavailable"}), 503
    path = folder_picker_callback()
    return jsonify({"ok": bool(path), "path": path or ""})


@app.route("/api/image/<int:item_id>")
def api_image(item_id):
    item = db.get_item(item_id)
    if not item:
        abort(404)
    full = request.args.get("full") == "1"
    if full:
        p = item.get("image_path")
    else:
        p = item.get("thumb_path") or item.get("image_path")
    if not p or not os.path.exists(p):
        abort(404)
    return send_file(p, mimetype="image/png")


@app.route("/api/copy/<int:item_id>", methods=["POST"])
def api_copy(item_id):
    item = db.get_item(item_id)
    if not item:
        abort(404)
    try:
        clipboard.suppress_next()
        if item["kind"] == "image":
            clipboard.copy_image_to_clipboard(item["image_path"])
        else:
            clipboard.copy_text_to_clipboard(item.get("content") or "")
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def api_delete(item_id):
    return jsonify({"ok": db.delete_item(item_id)})


@app.route("/api/items/<int:item_id>", methods=["PUT"])
def api_update(item_id):
    """更新某条记录的内容（文本/网址再次编辑）。"""
    item = db.get_item(item_id)
    if not item:
        abort(404)
    data = request.get_json(force=True, silent=True) or {}
    content = data.get("content")
    if content is None:
        return jsonify({"ok": False, "error": "missing content"}), 400
    db.update_content(item_id, content)
    return jsonify({"ok": True, "item": db.get_item(item_id)})


@app.route("/api/config", methods=["GET"])
def api_config_get():
    cfg = config.load_config()
    if hotkey_status_callback:
        cfg["hotkeys"] = hotkey_status_callback()
    cfg["autostart"] = autostart.get_status()
    return jsonify(cfg)


@app.route("/api/config", methods=["PUT"])
def api_config_put():
    data = request.get_json(force=True, silent=True) or {}
    requested_hotkey = data.get("hotkey")
    if not requested_hotkey:
        return jsonify({"ok": False, "error": "missing hotkey"}), 400
    cfg = config.load_config()
    cfg["hotkey"] = requested_hotkey
    if "obsidian_dir" in data:
        cfg["obsidian_dir"] = str(data["obsidian_dir"]).strip()
    if "obsidian_hotkey" in data:
        cfg["obsidian_hotkey"] = str(data["obsidian_hotkey"]).strip()
    if "monitor_paused" in data:
        cfg["monitor_paused"] = bool(data["monitor_paused"])
    if "sensitive_filter" in data:
        cfg["sensitive_filter"] = bool(data["sensitive_filter"])
    if "excluded_apps" in data:
        cfg["excluded_apps"] = [
            str(value).strip() for value in data["excluded_apps"] if str(value).strip()
        ]
    if "retention_days" in data:
        retention = int(data["retention_days"] or 0)
        cfg["retention_days"] = retention if retention in {0, 30, 90, 180} else 0
    config.save_config(cfg)
    active = None
    statuses = {}
    try:
        if reload_callback:
            # run.py 提供的统一入口：重注册主热键 + Obsidian 热键
            statuses = reload_callback() or {}
            active = (statuses.get("main") or {}).get("active")
        else:
            reloaded = hotkey_mod.manager.reload(requested_hotkey)
            if reloaded:
                active = reloaded
    except Exception:  # noqa: BLE001
        pass
    return jsonify({
        "ok": True,
        "hotkey": requested_hotkey,
        "active": active,
        "hotkeys": statuses,
    })


@app.route("/api/favorite/<int:item_id>", methods=["POST"])
def api_fav(item_id):
    fav = db.toggle_favorite(item_id)
    if fav is None:
        abort(404)
    return jsonify({"favorite": fav})


@app.route("/api/storage/duplicates", methods=["GET", "POST"])
def api_duplicates():
    if request.method == "GET":
        return jsonify(db.cleanup_duplicates(execute=False))
    return jsonify({"ok": True, **db.cleanup_duplicates(execute=True)})


def start_monitor():
    """启动后台剪贴板监听（仅 Windows 且未被禁用时）。"""
    enable = os.environ.get("CB_MONITOR", "1") != "0" and sys.platform.startswith("win")
    if enable:
        threading.Thread(target=clipboard.monitor_loop, daemon=True).start()


def start_flask():
    app.run(host="127.0.0.1", port=PORT, threaded=True)


def main():
    """兼容旧的浏览器模式。新版本请改用 run.py（原生窗口）。"""
    db.init_db()
    start_monitor()
    url = f"http://127.0.0.1:{PORT}"
    threading.Thread(
        target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True
    ).start()
    start_flask()


if __name__ == "__main__":
    main()
