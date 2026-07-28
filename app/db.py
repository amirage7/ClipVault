"""SQLite 存储层：剪贴板历史条目的增删查与统计。"""
import sqlite3
import os
import sys
import hashlib
import shutil
from datetime import datetime, timedelta

from .classifier import classify

def _repository_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def resolve_data_dir():
    """Return the durable data directory for the current runtime."""
    if not getattr(sys, "frozen", False):
        return os.path.join(_repository_root(), "data")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        local_app_data = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(local_app_data, "ClipVault", "data")


def legacy_data_dirs():
    """Return ordered legacy locations used by earlier frozen builds."""
    if not getattr(sys, "frozen", False):
        return []
    executable_dir = os.path.abspath(os.path.dirname(sys.executable))
    candidates = [os.path.join(executable_dir, "data")]
    if os.path.basename(executable_dir).lower() in {"dist", "release"}:
        candidates.append(os.path.join(os.path.dirname(executable_dir), "data"))
    unique = []
    for candidate in candidates:
        normalized = os.path.normcase(os.path.normpath(candidate))
        if all(os.path.normcase(os.path.normpath(path)) != normalized for path in unique):
            unique.append(candidate)
    return unique


def _copy_optional_file(source_dir, target_dir, name):
    source = os.path.join(source_dir, name)
    if os.path.isfile(source):
        shutil.copy2(source, os.path.join(target_dir, name))


def _copy_optional_tree(source_dir, target_dir, name):
    source = os.path.join(source_dir, name)
    if os.path.isdir(source):
        shutil.copytree(source, os.path.join(target_dir, name), dirs_exist_ok=True)


def _rewrite_migrated_image_paths(database_path, old_images_dir, new_images_dir):
    connection = None
    try:
        connection = sqlite3.connect(database_path)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(items)").fetchall()
        }
        if not {"image_path", "thumb_path"} <= columns:
            return
        for column in ("image_path", "thumb_path"):
            rows = connection.execute(
                f"SELECT id, {column} FROM items WHERE {column} IS NOT NULL"
            ).fetchall()
            for item_id, path in rows:
                try:
                    relative_path = os.path.relpath(path, old_images_dir)
                except ValueError:
                    continue
                if relative_path == os.pardir or relative_path.startswith(os.pardir + os.sep):
                    continue
                connection.execute(
                    f"UPDATE items SET {column}=? WHERE id=?",
                    (os.path.join(new_images_dir, relative_path), item_id),
                )
        connection.commit()
    except sqlite3.DatabaseError:
        return
    finally:
        if connection is not None:
            connection.close()


def migrate_legacy_data(canonical_data_dir, legacy_data_dirs):
    """Copy an old local store once, never changing or replacing its source."""
    canonical_db = os.path.join(canonical_data_dir, "clipboard.db")
    if os.path.exists(canonical_db):
        return None
    for legacy_dir in legacy_data_dirs:
        legacy_db = os.path.join(legacy_dir, "clipboard.db")
        if not os.path.isfile(legacy_db):
            continue
        os.makedirs(canonical_data_dir, exist_ok=True)
        shutil.copy2(legacy_db, canonical_db)
        _copy_optional_file(legacy_dir, canonical_data_dir, "config.json")
        _copy_optional_tree(legacy_dir, canonical_data_dir, "images")
        _copy_optional_tree(legacy_dir, canonical_data_dir, "backups")
        _rewrite_migrated_image_paths(
            canonical_db,
            os.path.join(legacy_dir, "images"),
            os.path.join(canonical_data_dir, "images"),
        )
        return legacy_dir
    return None


DATA_DIR = resolve_data_dir()
if getattr(sys, "frozen", False):
    migrate_legacy_data(DATA_DIR, legacy_data_dirs())
IMG_DIR = os.path.join(DATA_DIR, "images")
DB_PATH = os.path.join(DATA_DIR, "clipboard.db")


def content_signature(kind, value):
    payload = value if isinstance(value, bytes) else str(value or "").encode("utf-8")
    return hashlib.sha256(kind.encode("utf-8") + b"\0" + payload).hexdigest()


def _migration_backup():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = f"{DB_PATH}.pre-migration-{stamp}.bak"
    shutil.copy2(DB_PATH, backup)
    return backup


def init_db():
    os.makedirs(IMG_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """CREATE TABLE IF NOT EXISTS items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT NOT NULL,
            content     TEXT,
            image_path  TEXT,
            thumb_path  TEXT,
            favorite    INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL,
            source_app  TEXT,
            source_title TEXT,
            content_hash TEXT,
            copy_count INTEGER NOT NULL DEFAULT 1,
            last_copied_at TEXT,
            smart_category TEXT
        )"""
    )
    db.commit()
    columns = {row[1] for row in db.execute("PRAGMA table_info(items)").fetchall()}
    required = {
        "source_app": "TEXT",
        "source_title": "TEXT",
        "content_hash": "TEXT",
        "copy_count": "INTEGER NOT NULL DEFAULT 1",
        "last_copied_at": "TEXT",
        "smart_category": "TEXT",
    }
    missing = [name for name in required if name not in columns]
    if missing and os.path.getsize(DB_PATH) > 0:
        db.commit()
        _migration_backup()
    for name in missing:
        db.execute(f"ALTER TABLE items ADD COLUMN {name} {required[name]}")

    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, kind, content, image_path, created_at, content_hash, "
        "copy_count, last_copied_at, smart_category FROM items"
    ).fetchall()
    for row in rows:
        signature = row["content_hash"]
        if not signature:
            if row["kind"] == "image" and row["image_path"] and os.path.exists(row["image_path"]):
                with open(row["image_path"], "rb") as image_file:
                    signature = content_signature("image", image_file.read())
            else:
                signature = content_signature(row["kind"], row["content"] or "")
        db.execute(
            "UPDATE items SET content_hash=?, copy_count=COALESCE(copy_count,1), "
            "last_copied_at=COALESCE(last_copied_at,created_at), smart_category=? WHERE id=?",
            (signature, classify(row["kind"], row["content"]), row["id"]),
        )
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_hash ON items(content_hash)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_favorite ON items(favorite)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_items_smart_category ON items(smart_category)")
    db.commit()
    db.close()


def _conn():
    return sqlite3.connect(DB_PATH)


def backup_database(label="backup"):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = f"{DB_PATH}.{label}-{stamp}.bak"
    shutil.copy2(DB_PATH, backup)
    return backup


def add_item(kind, content=None, image_path=None, thumb_path=None, source_app=None, source_title=None,
             content_hash=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    signature = content_hash or content_signature(kind, content or "")
    smart_category = classify(kind, content)
    db = _conn()
    cur = db.execute(
        "INSERT INTO items (kind, content, image_path, thumb_path, favorite, created_at, "
        "source_app, source_title, content_hash, copy_count, last_copied_at, smart_category) "
        "VALUES (?,?,?,?,0,?,?,?,?,1,?,?)",
        (kind, content, image_path, thumb_path, now, source_app, source_title, signature, now, smart_category),
    )
    db.commit()
    item_id = cur.lastrowid
    db.close()
    return item_id


def upsert_item(kind, content=None, content_hash=None, source_app=None, source_title=None):
    """Insert a payload or refresh the newest matching unpinned record."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    signature = content_hash or content_signature(kind, content or "")
    smart_category = classify(kind, content)
    db = _conn()
    row = db.execute(
        "SELECT id FROM items WHERE content_hash=? AND favorite=0 "
        "ORDER BY id DESC LIMIT 1",
        (signature,),
    ).fetchone()
    if row:
        item_id = row[0]
        db.execute(
            "UPDATE items SET content=COALESCE(?,content), created_at=?, last_copied_at=?, "
            "source_app=COALESCE(?,source_app), source_title=COALESCE(?,source_title), "
            "copy_count=COALESCE(copy_count,1)+1, smart_category=? WHERE id=?",
            (content, now, now, source_app, source_title, smart_category, item_id),
        )
        db.commit()
        db.close()
        return item_id, False
    cur = db.execute(
        "INSERT INTO items (kind, content, favorite, created_at, source_app, source_title, "
        "content_hash, copy_count, last_copied_at, smart_category) VALUES (?,?,0,?,?,?,?,1,?,?)",
        (kind, content, now, source_app, source_title, signature, now, smart_category),
    )
    db.commit()
    item_id = cur.lastrowid
    db.close()
    return item_id, True


def update_paths(item_id, image_path, thumb_path):
    db = _conn()
    db.execute(
        "UPDATE items SET image_path=?, thumb_path=? WHERE id=?",
        (image_path, thumb_path, item_id),
    )
    db.commit()
    db.close()


def update_content(item_id, content):
    """更新某条记录的内容（用于文本/网址再次编辑）。"""
    db = _conn()
    row = db.execute("SELECT kind FROM items WHERE id=?", (item_id,)).fetchone()
    if row:
        db.execute(
            "UPDATE items SET content=?, content_hash=?, smart_category=? WHERE id=?",
            (content, content_signature(row[0], content), classify(row[0], content), item_id),
        )
    db.commit()
    db.close()


def _item_filters(kind="", q="", fav=None, days=None, start_date=None, end_date=None,
                  source=None, smart_category=None):
    sql = " WHERE 1=1"
    params = []
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    if source:
        sql += " AND source_app=?"
        params.append(source)
    if smart_category:
        sql += " AND smart_category=?"
        params.append(smart_category)
    if fav:
        sql += " AND favorite=1"
    if start_date or end_date:
        # start_date / end_date 格式为 YYYY-MM-DD
        if start_date:
            sql += " AND created_at >= ?"
            params.append(f"{start_date} 00:00:00")
        if end_date:
            sql += " AND created_at <= ?"
            params.append(f"{end_date} 23:59:59")
    elif days:
        sql += " AND created_at >= datetime('now', ?)"
        params.append("-%d days" % int(days))
    if q:
        sql += (
            " AND (content LIKE ? OR source_app LIKE ? OR source_title LIKE ?)"
        )
        needle = "%" + q + "%"
        params.extend([needle, needle, needle])
    return sql, params


def list_items(kind="", q="", limit=40, offset=0, fav=None, days=None, start_date=None,
               end_date=None, source=None, smart_category=None):
    db = _conn()
    db.row_factory = sqlite3.Row
    where, params = _item_filters(
        kind=kind, q=q, fav=fav, days=days, start_date=start_date,
        end_date=end_date, source=source, smart_category=smart_category,
    )
    sql = (
        "SELECT id, kind, content, image_path, thumb_path, favorite, created_at, "
        "source_app, source_title, content_hash, copy_count, last_copied_at, smart_category "
        "FROM items" + where +
        " ORDER BY favorite DESC, COALESCE(last_copied_at,created_at) DESC, id DESC "
        "LIMIT ? OFFSET ?"
    )
    params += [limit, offset]
    rows = db.execute(sql, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def count_items(kind="", q="", fav=None, days=None, start_date=None, end_date=None,
                source=None, smart_category=None):
    db = _conn()
    where, params = _item_filters(
        kind=kind, q=q, fav=fav, days=days, start_date=start_date,
        end_date=end_date, source=source, smart_category=smart_category,
    )
    count = db.execute("SELECT COUNT(*) FROM items" + where, params).fetchone()[0]
    db.close()
    return count


def cleanup_old_items(retention_days, now=None):
    days = int(retention_days or 0)
    if days <= 0:
        return 0
    threshold = (now or datetime.now()) - timedelta(days=days)
    cutoff = threshold.strftime("%Y-%m-%d %H:%M:%S")
    db = _conn()
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, image_path, thumb_path FROM items WHERE favorite=0 "
        "AND COALESCE(last_copied_at,created_at) < ?",
        (cutoff,),
    ).fetchall()
    for row in rows:
        for path in (row["image_path"], row["thumb_path"]):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
    db.executemany("DELETE FROM items WHERE id=?", [(row["id"],) for row in rows])
    db.commit()
    db.close()
    return len(rows)


def cleanup_duplicates(execute=False):
    db = _conn()
    db.row_factory = sqlite3.Row
    groups = db.execute(
        "SELECT content_hash FROM items WHERE content_hash IS NOT NULL "
        "GROUP BY content_hash HAVING COUNT(*) > 1"
    ).fetchall()
    deletable = []
    reclaimable = 0
    for group in groups:
        rows = db.execute(
            "SELECT id, favorite, image_path, thumb_path FROM items "
            "WHERE content_hash=? ORDER BY COALESCE(last_copied_at,created_at) DESC, id DESC",
            (group["content_hash"],),
        ).fetchall()
        newest_id = rows[0]["id"]
        for row in rows:
            if row["favorite"] or row["id"] == newest_id:
                continue
            deletable.append(row)
            for path in (row["image_path"], row["thumb_path"]):
                if path and os.path.exists(path):
                    reclaimable += os.path.getsize(path)
    preview = {"records": len(deletable), "bytes": reclaimable}
    if not execute:
        db.close()
        return preview
    db.close()
    backup = backup_database("before-dedup")
    db = _conn()
    for row in deletable:
        for path in (row["image_path"], row["thumb_path"]):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        db.execute("DELETE FROM items WHERE id=?", (row["id"],))
    db.commit()
    db.close()
    return {"deleted": len(deletable), "bytes": reclaimable, "backup": backup}


def get_item(item_id):
    db = _conn()
    db.row_factory = sqlite3.Row
    r = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    db.close()
    return dict(r) if r else None


def get_latest_item():
    db = _conn()
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT * FROM items ORDER BY COALESCE(last_copied_at,created_at) DESC, id DESC LIMIT 1"
    ).fetchone()
    db.close()
    return dict(row) if row else None


def delete_item(item_id):
    item = get_item(item_id)
    if not item:
        return False
    for p in (item.get("image_path"), item.get("thumb_path")):
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    db = _conn()
    db.execute("DELETE FROM items WHERE id=?", (item_id,))
    db.commit()
    db.close()
    return True


def clear_items():
    db = _conn()
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT image_path, thumb_path FROM items").fetchall()
    deleted = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    for row in rows:
        for p in (row["image_path"], row["thumb_path"]):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    db.execute("DELETE FROM items")
    db.commit()
    db.close()
    return deleted


def toggle_favorite(item_id):
    db = _conn()
    cur = db.execute("SELECT favorite FROM items WHERE id=?", (item_id,))
    r = cur.fetchone()
    if not r:
        db.close()
        return None
    new = 0 if r[0] else 1
    db.execute("UPDATE items SET favorite=? WHERE id=?", (new, item_id))
    db.commit()
    db.close()
    return new


def get_stats():
    db = _conn()
    rows = db.execute("SELECT kind, COUNT(*) c FROM items GROUP BY kind").fetchall()
    db.close()
    return {k: c for k, c in rows}
