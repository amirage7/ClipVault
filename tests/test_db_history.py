import glob
import hashlib
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from app import db


class DatabaseHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_img_dir = db.IMG_DIR
        db.DB_PATH = os.path.join(self.temp.name, "clipboard.db")
        db.IMG_DIR = os.path.join(self.temp.name, "images")

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.IMG_DIR = self.old_img_dir
        self.temp.cleanup()

    def test_init_migrates_legacy_database_without_losing_rows(self):
        legacy = sqlite3.connect(db.DB_PATH)
        legacy.execute(
            """CREATE TABLE items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                content TEXT,
                image_path TEXT,
                thumb_path TEXT,
                favorite INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                source_app TEXT,
                source_title TEXT
            )"""
        )
        legacy.execute(
            "INSERT INTO items(kind, content, favorite, created_at) VALUES(?,?,?,?)",
            ("text", "legacy text", 0, "2026-07-01 10:00:00"),
        )
        legacy.commit()
        legacy.close()

        db.init_db()

        migrated = sqlite3.connect(db.DB_PATH)
        columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(items)").fetchall()
        }
        row = migrated.execute(
            "SELECT content, content_hash, copy_count, last_copied_at, smart_category FROM items"
        ).fetchone()
        migrated.close()
        self.assertTrue(
            {"content_hash", "copy_count", "last_copied_at", "smart_category"}
            <= columns
        )
        self.assertEqual(row[0], "legacy text")
        self.assertEqual(
            row[1], hashlib.sha256(b"text\0legacy text").hexdigest()
        )
        self.assertEqual(row[2], 1)
        self.assertEqual(row[3], "2026-07-01 10:00:00")
        self.assertEqual(row[4], "")
        self.assertEqual(
            len(glob.glob(db.DB_PATH + ".pre-migration-*.bak")),
            1,
        )

    def test_frozen_build_uses_local_app_data(self):
        local_app_data = os.path.join(self.temp.name, "AppData", "Local")
        executable = os.path.join(self.temp.name, "Downloads", "ClipVault.exe")

        with mock.patch.object(db.sys, "frozen", True, create=True), \
                mock.patch.object(db.sys, "executable", executable), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": local_app_data}):
            resolved = db.resolve_data_dir()

        self.assertEqual(
            resolved,
            os.path.join(local_app_data, "ClipVault", "data"),
        )

    def test_migrate_legacy_data_copies_files_only_into_empty_store(self):
        legacy = os.path.join(self.temp.name, "Downloads", "data")
        canonical = os.path.join(self.temp.name, "AppData", "Local", "ClipVault", "data")
        os.makedirs(os.path.join(legacy, "images"))
        os.makedirs(os.path.join(legacy, "backups"))
        with open(os.path.join(legacy, "clipboard.db"), "wb") as handle:
            handle.write(b"database")
        with open(os.path.join(legacy, "config.json"), "w", encoding="utf-8") as handle:
            handle.write('{"hotkey":"ctrl+alt+c"}')
        with open(os.path.join(legacy, "images", "capture.png"), "wb") as handle:
            handle.write(b"image")
        with open(os.path.join(legacy, "backups", "clipboard.db.bak"), "wb") as handle:
            handle.write(b"backup")

        migrated_from = db.migrate_legacy_data(canonical, [legacy])

        self.assertEqual(migrated_from, legacy)
        with open(os.path.join(canonical, "clipboard.db"), "rb") as handle:
            self.assertEqual(handle.read(), b"database")
        with open(os.path.join(canonical, "config.json"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"hotkey":"ctrl+alt+c"}')
        with open(os.path.join(canonical, "images", "capture.png"), "rb") as handle:
            self.assertEqual(handle.read(), b"image")
        self.assertTrue(os.path.exists(os.path.join(canonical, "backups", "clipboard.db.bak")))

        with open(os.path.join(canonical, "clipboard.db"), "wb") as handle:
            handle.write(b"canonical")
        self.assertIsNone(db.migrate_legacy_data(canonical, [legacy]))
        with open(os.path.join(canonical, "clipboard.db"), "rb") as handle:
            self.assertEqual(handle.read(), b"canonical")

    def test_frozen_build_discovers_data_beside_executable_and_release_parent(self):
        executable = os.path.join(self.temp.name, "release", "ClipVault.exe")

        with mock.patch.object(db.sys, "frozen", True, create=True), \
                mock.patch.object(db.sys, "executable", executable):
            candidates = db.legacy_data_dirs()

        self.assertEqual(
            candidates,
            [
                os.path.join(self.temp.name, "release", "data"),
                os.path.join(self.temp.name, "data"),
            ],
        )

    def test_upsert_reuses_unpinned_text_and_increments_copy_count(self):
        db.init_db()
        signature = db.content_signature("text", "same text")

        first_id, first_created = db.upsert_item(
            "text",
            content="same text",
            content_hash=signature,
            source_app="Edge",
            source_title="First",
        )
        second_id, second_created = db.upsert_item(
            "text",
            content="same text",
            content_hash=signature,
            source_app="WPS",
            source_title="Second",
        )

        item = db.get_item(first_id)
        self.assertEqual(first_id, second_id)
        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(item["copy_count"], 2)
        self.assertEqual(item["source_app"], "WPS")
        self.assertEqual(item["source_title"], "Second")
        self.assertEqual(db.get_stats()["text"], 1)

    def test_upsert_does_not_overwrite_pinned_match(self):
        db.init_db()
        signature = db.content_signature("text", "keep pinned")
        pinned_id, _ = db.upsert_item(
            "text", content="keep pinned", content_hash=signature
        )
        db.toggle_favorite(pinned_id)

        new_id, created = db.upsert_item(
            "text", content="keep pinned", content_hash=signature
        )

        self.assertTrue(created)
        self.assertNotEqual(new_id, pinned_id)
        self.assertEqual(db.get_stats()["text"], 2)

    def test_paging_reaches_oldest_record_and_searches_source_metadata(self):
        db.init_db()
        for index in range(120):
            db.add_item(
                "text",
                content=f"entry {index}",
                source_app="WPS" if index == 3 else "Edge",
                source_title="Annual report" if index == 4 else "Window",
            )

        last_page = db.list_items(limit=50, offset=100)

        self.assertEqual(db.count_items(), 120)
        self.assertEqual(len(last_page), 20)
        self.assertIn("entry 0", [item["content"] for item in last_page])
        self.assertEqual(db.count_items(q="WPS"), 1)
        self.assertEqual(db.count_items(q="Annual report"), 1)

    def test_smart_category_updates_on_insert_edit_and_filters_pages(self):
        db.init_db()
        prompt_id, _created = db.upsert_item(
            "text", content="帮我分析一下这份报告"
        )
        code_id = db.add_item("text", content="def answer():\n    return 42")

        self.assertEqual(db.get_item(prompt_id)["smart_category"], "prompt")
        self.assertEqual(db.get_item(code_id)["smart_category"], "code")
        self.assertEqual(db.count_items(smart_category="code"), 1)
        self.assertEqual(
            [item["id"] for item in db.list_items(smart_category="code")],
            [code_id],
        )

        db.update_content(prompt_id, "TODO: 明天提交报告")

        self.assertEqual(db.get_item(prompt_id)["smart_category"], "todo")
        self.assertEqual(db.count_items(smart_category="prompt"), 0)
        self.assertEqual(db.count_items(smart_category="todo"), 1)

    def test_retention_deletes_old_unpinned_items_only(self):
        db.init_db()
        old_id = db.add_item("text", content="old")
        pinned_id = db.add_item("text", content="old pinned")
        recent_id = db.add_item("text", content="recent")
        db.toggle_favorite(pinned_id)
        connection = sqlite3.connect(db.DB_PATH)
        connection.execute(
            "UPDATE items SET created_at=?, last_copied_at=? WHERE id IN (?,?)",
            ("2026-01-01 00:00:00", "2026-01-01 00:00:00", old_id, pinned_id),
        )
        connection.commit()
        connection.close()

        deleted = db.cleanup_old_items(30, now=datetime(2026, 7, 26, 12, 0, 0))

        self.assertEqual(deleted, 1)
        self.assertIsNone(db.get_item(old_id))
        self.assertIsNotNone(db.get_item(pinned_id))
        self.assertIsNotNone(db.get_item(recent_id))

    def test_duplicate_cleanup_previews_then_creates_backup_and_removes_extras(self):
        db.init_db()
        signature = db.content_signature("text", "duplicate")
        for _index in range(3):
            db.add_item("text", content="duplicate", content_hash=signature)

        preview = db.cleanup_duplicates(execute=False)
        result = db.cleanup_duplicates(execute=True)

        self.assertEqual(preview["records"], 2)
        self.assertEqual(result["deleted"], 2)
        self.assertTrue(os.path.exists(result["backup"]))
        self.assertEqual(db.count_items(), 1)


if __name__ == "__main__":
    unittest.main()
