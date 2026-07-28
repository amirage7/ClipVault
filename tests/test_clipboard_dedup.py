import io
import os
import tempfile
import unittest
from unittest import mock

from PIL import Image

from app import clipboard, db


class ClipboardDedupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_img_dir = db.IMG_DIR
        db.DB_PATH = os.path.join(self.temp.name, "clipboard.db")
        db.IMG_DIR = os.path.join(self.temp.name, "images")
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.IMG_DIR = self.old_img_dir
        self.temp.cleanup()

    def test_storing_identical_image_twice_reuses_row_and_files(self):
        image = Image.new("RGB", (16, 16), "red")
        payload = io.BytesIO()
        image.save(payload, "PNG")
        data = (clipboard.KIND_IMAGE, payload.getvalue())

        with mock.patch.object(
            clipboard, "get_active_app", return_value=("Edge", "Image page")
        ):
            first = clipboard._store_clipboard_data(data)
            second = clipboard._store_clipboard_data(data)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(db.get_stats()["image"], 1)
        self.assertEqual(second["copy_count"], 2)
        self.assertTrue(os.path.exists(second["image_path"]))
        self.assertTrue(os.path.exists(second["thumb_path"]))
        self.assertEqual(len(os.listdir(db.IMG_DIR)), 2)

    def test_sensitive_filter_and_excluded_apps(self):
        settings = {
            "monitor_paused": False,
            "sensitive_filter": True,
            "excluded_apps": ["Password Manager"],
        }

        self.assertFalse(
            clipboard.should_store(
                clipboard.KIND_TEXT, "password: hunter2", "Edge", settings
            )
        )
        self.assertFalse(
            clipboard.should_store(
                clipboard.KIND_TEXT, "ordinary", "Password Manager", settings
            )
        )
        self.assertTrue(
            clipboard.should_store(
                clipboard.KIND_TEXT, "ordinary", "Edge", settings
            )
        )
        self.assertTrue(
            clipboard.should_store(
                clipboard.KIND_TEXT,
                "这是一段产品说明，里面提到密码管理器、验证码过滤和本地隐私策略，但不是具体密码。",
                "Edge",
                settings,
            )
        )
        settings["monitor_paused"] = True
        self.assertFalse(
            clipboard.should_store(
                clipboard.KIND_TEXT, "ordinary", "Edge", settings
            )
        )


if __name__ == "__main__":
    unittest.main()
