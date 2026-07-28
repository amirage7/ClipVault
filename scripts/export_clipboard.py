import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "clipboard.db"
EXPORT_ROOT = ROOT / "exports"


def rel_for_markdown(path):
    return path.as_posix()


def fence_for(content):
    return "````" if "```" in content else "```"


def main():
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = EXPORT_ROOT / ("clipboard-export-" + ts)
    images_dir = out_dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT id, kind, content, image_path, thumb_path, favorite, "
            "created_at, source_app, source_title FROM items ORDER BY id ASC"
        )
    ]
    db.close()

    manifest = []
    copied = {}
    markdown = [
        "# ClipVault clipboard export",
        "",
        "- Exported at: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- Records: " + str(len(rows)),
        "",
    ]

    for row in rows:
        record = dict(row)
        record["image_file"] = None
        record["thumb_file"] = None

        for source_key, export_key in (
            ("image_path", "image_file"),
            ("thumb_path", "thumb_file"),
        ):
            source_value = record.get(source_key)
            if not source_value:
                continue
            source_path = Path(source_value)
            if not source_path.exists():
                continue

            if str(source_path) not in copied:
                dest_name = str(record["id"]) + "_" + source_path.name
                dest_path = images_dir / dest_name
                shutil.copy2(source_path, dest_path)
                copied[str(source_path)] = rel_for_markdown(Path("images") / dest_name)
            record[export_key] = copied[str(source_path)]

        manifest.append(record)

        markdown.extend(
            [
                "## #{} | {} | {}".format(
                    record["id"], record["kind"], record["created_at"]
                ),
                "",
                "- Source app: " + str(record.get("source_app") or ""),
                "- Source title: " + str(record.get("source_title") or ""),
                "- Favorite: " + ("yes" if record.get("favorite") else "no"),
                "",
            ]
        )

        if record["kind"] == "image":
            if record.get("image_file"):
                markdown.extend(["![image](" + record["image_file"] + ")", ""])
            else:
                markdown.extend(["[image file missing]", ""])
        else:
            content = record.get("content") or ""
            fence = fence_for(content)
            markdown.extend([fence, content, fence, ""])

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "clipboard-export.md").write_text(
        "\n".join(markdown), encoding="utf-8"
    )
    shutil.copy2(DB_PATH, out_dir / "clipboard.db")
    zip_path = shutil.make_archive(str(out_dir), "zip", out_dir)

    print(
        json.dumps(
            {
                "directory": str(out_dir),
                "zip": zip_path,
                "records": len(rows),
                "files": len(list(images_dir.glob("*"))),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
