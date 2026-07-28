import types
import unittest

import run


class RunStartupTests(unittest.TestCase):
    def test_main_starts_flask_thread_after_initialization(self):
        original = {
            "ensure": run._ensure_single_instance,
            "platform": run.sys.platform,
            "hotkey": getattr(run, "hotkey_mod", None),
            "config": getattr(run, "config_mod", None),
            "thread": run.threading.Thread,
            "wait": run._wait_for_server,
            "monitor": run.server.start_monitor,
            "db_init": run.server.db.init_db,
            "reload_callback": run.server.reload_callback,
            "status_callback": run.server.hotkey_status_callback,
        }
        started = []

        class FakeManager:
            def __init__(self, *args, **kwargs):
                pass

            def get_statuses(self):
                return {}

        class FakeThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                started.append(self.target)

        try:
            run._ensure_single_instance = lambda: True
            run.sys.platform = "test"
            run.hotkey_mod = types.SimpleNamespace(
                HotkeyManager=FakeManager,
                manager=FakeManager(),
                FALLBACKS=[],
            )
            run.config_mod = types.SimpleNamespace(load_config=lambda: {})
            run.threading.Thread = FakeThread
            run._wait_for_server = lambda _port: False
            run.server.start_monitor = lambda: None
            run.server.db.init_db = lambda: None

            run.main()

            self.assertEqual(started.count(run.server.start_flask), 1)
        finally:
            run._ensure_single_instance = original["ensure"]
            run.sys.platform = original["platform"]
            run.threading.Thread = original["thread"]
            run._wait_for_server = original["wait"]
            run.server.start_monitor = original["monitor"]
            run.server.db.init_db = original["db_init"]
            run.server.reload_callback = original["reload_callback"]
            run.server.hotkey_status_callback = original["status_callback"]
            if original["hotkey"] is None:
                delattr(run, "hotkey_mod")
            else:
                run.hotkey_mod = original["hotkey"]
            if original["config"] is None:
                delattr(run, "config_mod")
            else:
                run.config_mod = original["config"]


if __name__ == "__main__":
    unittest.main()
