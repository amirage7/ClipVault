import unittest
import time
import threading

from app import hotkey


class HotkeyRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.old_register_hotkey = hotkey.win32gui.RegisterHotKey
        self.old_register_class = hotkey.win32gui.RegisterClass
        self.old_post_message = hotkey.win32gui.PostMessage

    def tearDown(self):
        hotkey.win32gui.RegisterHotKey = self.old_register_hotkey
        hotkey.win32gui.RegisterClass = self.old_register_class
        hotkey.win32gui.PostMessage = self.old_post_message

    def test_reuses_existing_window_class_after_hotkey_reload(self):
        class ClassAlreadyExists(Exception):
            winerror = 1410

        def fake_register_class(window_class):
            raise ClassAlreadyExists("already exists")

        hotkey.win32gui.RegisterClass = fake_register_class

        registered = hotkey._register_window_class(object(), "ClipVaultTestHotkeyClass")

        self.assertEqual(registered, "ClipVaultTestHotkeyClass")

    def test_unregister_keeps_handle_for_message_thread_cleanup(self):
        calls = []
        manager = hotkey.HotkeyManager()
        manager._hwnd = 101
        hotkey.win32gui.PostMessage = lambda *args: calls.append(args)

        manager.unregister()

        self.assertEqual(len(calls), 1)
        self.assertEqual(manager._hwnd, 101)

    def test_register_waits_until_hotkey_result_is_available(self):
        manager = hotkey.HotkeyManager()

        def fake_loop(_candidates):
            manager._ready.set()
            time.sleep(0.02)
            manager.active = "ctrl+alt+o"
            manager._registered_ready.set()

        manager._loop = fake_loop
        active = manager.register(None, lambda: None, "ctrl+alt+o")

        self.assertEqual(active, "ctrl+alt+o")

    def test_register_hotkey_treats_no_exception_as_success(self):
        calls = []

        def fake_register_hotkey(hwnd, hotkey_id, modifiers, vk):
            calls.append((hwnd, hotkey_id, modifiers, vk))
            return None

        hotkey.win32gui.RegisterHotKey = fake_register_hotkey

        registered = hotkey._register_hotkey(100, "ctrl+alt+c", hotkey.parse("ctrl+alt+c"))

        self.assertTrue(registered)
        self.assertEqual(len(calls), 1)

    def test_register_hotkey_treats_exception_as_failure(self):
        def fake_register_hotkey(hwnd, hotkey_id, modifiers, vk):
            raise RuntimeError("already registered")

        hotkey.win32gui.RegisterHotKey = fake_register_hotkey

        registered = hotkey._register_hotkey(100, "ctrl+alt+c", hotkey.parse("ctrl+alt+c"))

        self.assertFalse(registered)

    def test_register_many_registers_both_shortcuts_on_owner_thread(self):
        caller_thread = threading.get_ident()
        registrations = []
        original = {
            "wndclass": hotkey.win32gui.WNDCLASS,
            "module": hotkey.win32api.GetModuleHandle,
            "cursor": hotkey.win32gui.LoadCursor,
            "create": hotkey.win32gui.CreateWindow,
            "register": hotkey.win32gui.RegisterHotKey,
            "pump": hotkey.win32gui.PumpMessages,
            "unregister": hotkey.win32gui.UnregisterHotKey,
            "destroy": hotkey.win32gui.DestroyWindow,
        }

        class FakeWindowClass:
            pass

        try:
            hotkey.win32gui.WNDCLASS = FakeWindowClass
            hotkey.win32api.GetModuleHandle = lambda _value: 1
            hotkey.win32gui.LoadCursor = lambda *_args: 1
            hotkey.win32gui.RegisterClass = lambda _wc: "ClipVaultTest"
            hotkey.win32gui.CreateWindow = lambda *_args: 701
            hotkey.win32gui.RegisterHotKey = lambda _hwnd, hotkey_id, _mods, _vk: (
                registrations.append((threading.get_ident(), hotkey_id))
            )
            hotkey.win32gui.PumpMessages = lambda: None
            hotkey.win32gui.UnregisterHotKey = lambda *_args: None
            hotkey.win32gui.DestroyWindow = lambda _hwnd: None

            manager = hotkey.HotkeyManager(class_name="ClipVaultTestMany")
            statuses = manager.register_many(
                [
                    {"name": "main", "id": 1, "preferred": "ctrl+e", "callback": lambda: None},
                    {"name": "obsidian", "id": 2, "preferred": "ctrl+alt+o", "callback": lambda: None, "fallbacks": []},
                ]
            )
            manager.thread.join(timeout=1)
        finally:
            hotkey.win32gui.WNDCLASS = original["wndclass"]
            hotkey.win32api.GetModuleHandle = original["module"]
            hotkey.win32gui.LoadCursor = original["cursor"]
            hotkey.win32gui.CreateWindow = original["create"]
            hotkey.win32gui.RegisterHotKey = original["register"]
            hotkey.win32gui.PumpMessages = original["pump"]
            hotkey.win32gui.UnregisterHotKey = original["unregister"]
            hotkey.win32gui.DestroyWindow = original["destroy"]

        self.assertEqual([hotkey_id for _thread, hotkey_id in registrations], [1, 2])
        self.assertTrue(all(thread_id != caller_thread for thread_id, _id in registrations))
        self.assertTrue(statuses["main"]["ok"])
        self.assertEqual(statuses["main"]["active"], "ctrl+e")
        self.assertTrue(statuses["obsidian"]["ok"])
        self.assertEqual(statuses["obsidian"]["active"], "ctrl+alt+o")


if __name__ == "__main__":
    unittest.main()
