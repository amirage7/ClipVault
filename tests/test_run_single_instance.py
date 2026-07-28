import sys
import types
import unittest
from unittest import mock

import run


class SingleInstanceTests(unittest.TestCase):
    def test_find_hwnd_ignores_same_title_window_from_other_process(self):
        fake_win32gui = types.SimpleNamespace(
            FindWindow=lambda _class, _title: 10,
            GetWindowText=lambda _hwnd: run.APP_TITLE,
            EnumWindows=lambda callback, lparam: [
                callback(hwnd, lparam) for hwnd in (10, 20)
            ],
        )
        fake_win32process = types.SimpleNamespace(
            GetWindowThreadProcessId=lambda hwnd: (1, 999 if hwnd == 10 else 123)
        )

        with mock.patch.object(run.os, "getpid", return_value=123), mock.patch.dict(
            sys.modules,
            {
                "win32gui": fake_win32gui,
                "win32process": fake_win32process,
            },
        ):
            self.assertEqual(run._find_hwnd(run.APP_TITLE), 20)

    def test_focus_webview_control_invokes_official_control_focus(self):
        focused = []

        class FakeControl:
            def Focus(self):
                focused.append(True)

        class FakeInstance:
            browser = types.SimpleNamespace(webview=FakeControl())

            def Invoke(self, action):
                action()

        fake_platform = types.SimpleNamespace(
            BrowserView=types.SimpleNamespace(instances={"main": FakeInstance()})
        )
        fake_system = types.SimpleNamespace(Action=lambda callback: callback)
        window = types.SimpleNamespace(uid="main")

        with mock.patch(
            "importlib.import_module", return_value=fake_platform
        ), mock.patch.dict(sys.modules, {"System": fake_system}):
            self.assertTrue(run._focus_webview_control(window))

        self.assertEqual(focused, [True])

    def test_force_foreground_attaches_calling_thread_to_window_threads(self):
        calls = []
        fake_win32api = types.SimpleNamespace(
            GetCurrentThreadId=lambda: 100,
            AllowSetForegroundWindow=lambda _pid: None,
        )
        fake_win32con = types.SimpleNamespace(
            SW_RESTORE=9,
            HWND_TOPMOST=-1,
            HWND_NOTOPMOST=-2,
            SWP_NOMOVE=2,
            SWP_NOSIZE=1,
        )
        fake_win32gui = types.SimpleNamespace(
            IsIconic=lambda _hwnd: False,
            GetForegroundWindow=lambda: 2000,
            SetForegroundWindow=lambda hwnd: calls.append(("foreground", hwnd)),
            BringWindowToTop=lambda hwnd: calls.append(("top", hwnd)),
            SetActiveWindow=lambda hwnd: calls.append(("active", hwnd)),
            SetWindowPos=lambda *args: None,
        )
        fake_win32process = types.SimpleNamespace(
            GetWindowThreadProcessId=lambda hwnd: (300 if hwnd == 1000 else 200, 0),
            AttachThreadInput=lambda source, target, attach: calls.append(
                ("attach", source, target, attach)
            ),
        )

        with mock.patch.object(run, "_find_hwnd", return_value=1000), mock.patch.dict(
            sys.modules,
            {
                "win32api": fake_win32api,
                "win32con": fake_win32con,
                "win32gui": fake_win32gui,
                "win32process": fake_win32process,
            },
        ):
            run._force_foreground()

        self.assertIn(("attach", 100, 200, True), calls)
        self.assertIn(("attach", 100, 300, True), calls)
        self.assertIn(("foreground", 1000), calls)
        self.assertIn(("active", 1000), calls)
        self.assertIn(("attach", 100, 300, False), calls)
        self.assertIn(("attach", 100, 200, False), calls)

    def test_input_window_class_recognizes_native_edit_controls(self):
        self.assertTrue(run._is_editable_window_class("Edit"))
        self.assertTrue(run._is_editable_window_class("RICHEDIT50W"))
        self.assertFalse(run._is_editable_window_class("CabinetWClass"))

    def test_webview_class_recognizes_edge_keyboard_target(self):
        self.assertTrue(run._is_webview_class("Chrome_RenderWidgetHostHWND"))
        self.assertTrue(run._is_webview_class("Chrome_WidgetWin_0"))
        self.assertFalse(run._is_webview_class("WindowsForms10.Window.8.app"))

    def test_paste_target_falls_back_to_last_foreground_window(self):
        old_target = run.APP_STATE.get("paste_target")
        old_focused = run.APP_STATE.get("last_focused")
        try:
            run.APP_STATE["paste_target"] = None
            run.APP_STATE["last_focused"] = 9876
            self.assertEqual(run._paste_target(), 9876)
        finally:
            run.APP_STATE["paste_target"] = old_target
            run.APP_STATE["last_focused"] = old_focused

    def test_text_paste_copies_then_sends_ctrl_v_to_captured_target(self):
        copied = []
        sent = []
        old_target = run.APP_STATE.get("paste_target")
        old_focused = run.APP_STATE.get("last_focused")
        with mock.patch.object(
            run.server.db, "get_item", return_value={"kind": "text", "content": "hello"}
        ), mock.patch.object(run, "_hide_our_window"), mock.patch.object(
            run, "_restore_focus", return_value=True
        ) as restore, mock.patch.object(
            run.server.clipboard, "copy_text_to_clipboard", side_effect=copied.append
        ), mock.patch.object(run.server.clipboard, "suppress_next"), mock.patch.object(
            run, "_send_ctrl_v", side_effect=lambda: sent.append(True)
        ), mock.patch.object(run, "_send_unicode_text") as type_text, mock.patch.object(
            run.time, "sleep"
        ):
            try:
                run.APP_STATE["paste_target"] = 6789
                run.APP_STATE["last_focused"] = None
                self.assertTrue(run.paste_item(1))
            finally:
                run.APP_STATE["paste_target"] = old_target
                run.APP_STATE["last_focused"] = old_focused

        restore.assert_called_once_with(6789)
        self.assertEqual(copied, ["hello"])
        self.assertEqual(sent, [True])
        type_text.assert_not_called()

    def test_existing_instance_requests_running_app_to_show(self):
        mutex_names = []
        fake_win32api = types.SimpleNamespace(GetLastError=lambda: 183)
        fake_winerror = types.SimpleNamespace(ERROR_ALREADY_EXISTS=183)

        def fake_create_mutex(security, initial_owner, name):
            mutex_names.append(name)
            return object()

        fake_win32event = types.SimpleNamespace(CreateMutex=fake_create_mutex)

        with mock.patch.object(run.sys, "platform", "win32"):
            with mock.patch.dict(
                sys.modules,
                {
                    "win32api": fake_win32api,
                    "winerror": fake_winerror,
                    "win32event": fake_win32event,
                },
            ):
                with mock.patch.object(run, "_port_in_use", return_value=False):
                    with mock.patch.object(run, "_activate_existing_instance") as activate:
                        result = run._ensure_single_instance()

        self.assertFalse(result)
        self.assertTrue(mutex_names)
        self.assertTrue(all(name == "Local\\ClipVaultSingleInstance" for name in mutex_names))
        activate.assert_called_once()

    def test_obsidian_push_exports_current_system_clipboard_before_history(self):
        current = {"id": 9, "kind": "text", "content": "current clipboard"}
        with mock.patch.object(
            run.server.clipboard, "capture_current_item", return_value=current, create=True
        ) as capture, mock.patch.object(
            run.server.db, "get_latest_item", return_value={"id": 1, "content": "stale"}
        ) as latest, mock.patch("app.obsidian.export_item", return_value="C:/note.md") as export, mock.patch(
            "app.config.load_config", return_value={"obsidian_dir": "C:/vault"}
        ), mock.patch("app.notify.notify"):
            result = run.push_latest_to_obsidian()

        self.assertEqual(result, "C:/note.md")
        capture.assert_called_once_with()
        latest.assert_not_called()
        export.assert_called_once_with(current, "C:/vault")


if __name__ == "__main__":
    unittest.main()
