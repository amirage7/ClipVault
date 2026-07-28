"""Export clipboard records as Markdown files readable by Obsidian."""
import os
import shutil
from datetime import datetime


def export_item(item, target_dir):
    if not target_dir:
        raise ValueError("请先设置 Obsidian 目标文件夹")
    os.makedirs(target_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    content = item.get("content") or ""
    title = f"剪贴板记录 {stamp}"
    note_path = os.path.join(target_dir, f"{stamp} {title}.md")
    if item.get("kind") == "image":
        source = item.get("image_path")
        if not source or not os.path.exists(source):
            raise ValueError("图片文件不存在")
        assets = os.path.join(target_dir, "attachments")
        os.makedirs(assets, exist_ok=True)
        image_name = f"{stamp}.png"
        shutil.copy2(source, os.path.join(assets, image_name))
        body = f"![[attachments/{image_name}]]\n"
    elif item.get("kind") == "url":
        body = f"[{content}]({content})\n"
    else:
        body = content + "\n"
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(f"---\ntitle: {title}\ncreated: {datetime.now().isoformat(timespec='seconds')}\nsource: ClipVault\n---\n\n{body}")
    return note_path
