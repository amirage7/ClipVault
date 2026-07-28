"""应用配置：目前主要保存可自定义的全局快捷键。

配置保存在 data/config.json（打包成 exe 后位于可执行文件同级的 data 目录下），
与数据库同一目录，保证数据/配置都留在本地、不依赖云端。
"""
import json
import os

from .db import DATA_DIR

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DEFAULTS = {
    "hotkey": "ctrl+alt+c",
    "obsidian_hotkey": "ctrl+alt+o",
    "obsidian_dir": "",
    "monitor_paused": False,
    "sensitive_filter": True,
    "excluded_apps": [],
    "retention_days": 0,
}


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = dict(DEFAULTS)
        save_config(data)
    for k, v in DEFAULTS.items():
        data.setdefault(k, v)
    return data


def save_config(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def get_hotkey():
    return load_config().get("hotkey", DEFAULTS["hotkey"])
