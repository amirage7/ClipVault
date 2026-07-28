import os
import tempfile
import unittest

from app import obsidian


class ObsidianExportTests(unittest.TestCase):
    def test_exports_text_as_markdown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = obsidian.export_item({"kind": "text", "content": "hello"}, folder)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as note:
                self.assertIn("hello", note.read())
            self.assertIn("剪贴板记录", os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
