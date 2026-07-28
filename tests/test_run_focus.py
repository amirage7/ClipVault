import sys
import types
import unittest
from unittest import mock

import run


class RunFocusTests(unittest.TestCase):
    def _restore_with_state(self, minimized):
        shown = []
        foreground = [0]

        def set_foreground(hwnd):
            foreground[0] = hwnd

        fake_gui = types.SimpleNamespace(
            IsWindow=lambda _hwnd: True,
            IsIconic=lambda _hwnd: minimized,
            ShowWindow=lambda hwnd, command: shown.append((hwnd, command)),
            SetForegroundWindow=set_foreground,
            GetForegroundWindow=lambda: foreground[0],
        )
        fake_process = types.SimpleNamespace(
            GetWindowThreadProcessId=lambda _hwnd: (123, 456),
            AttachThreadInput=lambda *_args: None,
        )
        fake_con = types.SimpleNamespace(ASFW_ANY=-1, SW_RESTORE=9)
        fake_api = types.SimpleNamespace(
            GetCurrentThreadId=lambda: 123,
            AllowSetForegroundWindow=lambda _value: None,
        )
        modules = {
            "win32gui": fake_gui,
            "win32process": fake_process,
            "win32con": fake_con,
            "win32api": fake_api,
        }
        with mock.patch.dict(sys.modules, modules), mock.patch.object(run.time, "sleep"):
            result = run._restore_focus(9001, retries=1)
        return result, shown

    def test_restore_focus_does_not_restore_normal_or_maximized_window(self):
        result, shown = self._restore_with_state(minimized=False)

        self.assertTrue(result)
        self.assertEqual(shown, [])

    def test_restore_focus_restores_minimized_window(self):
        result, shown = self._restore_with_state(minimized=True)

        self.assertTrue(result)
        self.assertEqual(shown, [(9001, 9)])


if __name__ == "__main__":
    unittest.main()
