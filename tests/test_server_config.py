import unittest

from app import server


class FakeManager:
    def __init__(self, active):
        self.active = active
        self.calls = []

    def reload(self, hotkey):
        self.calls.append(hotkey)
        return self.active


class ServerConfigTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.old_load_config = server.config.load_config
        self.old_save_config = server.config.save_config
        self.old_manager = server.hotkey_mod.manager
        self.old_clear_items = getattr(server.db, "clear_items", None)
        self.old_show_callback = server.show_callback
        self.old_reload_callback = server.reload_callback
        server.reload_callback = None

    def tearDown(self):
        server.config.load_config = self.old_load_config
        server.config.save_config = self.old_save_config
        server.hotkey_mod.manager = self.old_manager
        if self.old_clear_items is None:
            try:
                delattr(server.db, "clear_items")
            except AttributeError:
                pass
        else:
            server.db.clear_items = self.old_clear_items
        server.show_callback = self.old_show_callback
        server.reload_callback = self.old_reload_callback

    def test_config_put_reloads_runtime_hotkey_and_reports_active_combo(self):
        saved = []
        statuses = {
            "main": {"requested": "ctrl+shift+v", "active": "ctrl+shift+v", "ok": True, "error": ""},
            "obsidian": {"requested": "ctrl+alt+o", "active": "ctrl+alt+o", "ok": True, "error": ""},
        }
        server.reload_callback = lambda: statuses
        server.config.load_config = lambda: {
            "hotkey": "ctrl+win+v",
            "obsidian_hotkey": "ctrl+alt+o",
        }
        server.config.save_config = lambda cfg: saved.append(dict(cfg)) or cfg

        response = self.client.put(
            "/api/config",
            json={"hotkey": "ctrl+shift+v", "obsidian_hotkey": "ctrl+alt+o"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            saved,
            [{"hotkey": "ctrl+shift+v", "obsidian_hotkey": "ctrl+alt+o"}],
        )
        self.assertEqual(
            response.get_json(),
            {
                "ok": True,
                "hotkey": "ctrl+shift+v",
                "active": "ctrl+shift+v",
                "hotkeys": statuses,
            },
        )

    def test_delete_items_clears_all_records(self):
        calls = []

        def fake_clear_items():
            calls.append("clear")
            return 12

        server.db.clear_items = fake_clear_items

        response = self.client.delete("/api/items")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, ["clear"])
        self.assertEqual(response.get_json(), {"ok": True, "deleted": 12})

    def test_show_route_invokes_registered_callback(self):
        calls = []
        server.set_show_callback(lambda: calls.append("show"))

        response = self.client.post("/api/show")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, ["show"])
        self.assertEqual(response.get_json(), {"ok": True})

    def test_items_route_returns_paging_envelope_and_clamps_limit(self):
        old_list = server.db.list_items
        old_count = getattr(server.db, "count_items", None)
        calls = []
        try:
            server.db.list_items = lambda **kwargs: calls.append(kwargs) or [{"id": 1}]
            server.db.count_items = lambda **kwargs: 382

            response = self.client.get("/api/items?limit=999&offset=50&q=WPS")
        finally:
            server.db.list_items = old_list
            if old_count is None:
                delattr(server.db, "count_items")
            else:
                server.db.count_items = old_count

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0]["limit"], 100)
        self.assertEqual(
            response.get_json(),
            {
                "items": [{"id": 1}],
                "total": 382,
                "limit": 100,
                "offset": 50,
                "has_more": True,
            },
        )

    def test_items_route_passes_valid_smart_category_to_list_and_count(self):
        old_list = server.db.list_items
        old_count = server.db.count_items
        list_calls = []
        count_calls = []
        try:
            server.db.list_items = lambda **kwargs: list_calls.append(kwargs) or []
            server.db.count_items = lambda **kwargs: count_calls.append(kwargs) or 0

            response = self.client.get("/api/items?smart_category=code")
        finally:
            server.db.list_items = old_list
            server.db.count_items = old_count

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list_calls[0]["smart_category"], "code")
        self.assertEqual(count_calls[0]["smart_category"], "code")


if __name__ == "__main__":
    unittest.main()
