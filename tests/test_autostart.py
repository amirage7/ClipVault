import types
import unittest
from unittest import mock

from app import autostart


class AutostartTests(unittest.TestCase):
    def test_enable_writes_current_user_run_value(self):
        calls = []
        key = mock.MagicMock()
        key.__enter__.return_value = key
        fake_winreg = types.SimpleNamespace(
            HKEY_CURRENT_USER=1,
            KEY_SET_VALUE=2,
            REG_SZ=3,
            OpenKey=lambda *args: calls.append(("open", args)) or key,
            SetValueEx=lambda *args: calls.append(("set", args)),
        )

        autostart.enable_with_registry(fake_winreg, '"C:\\App\\ClipVault.exe"')

        self.assertEqual(calls[0][0], "open")
        self.assertEqual(calls[1], ("set", (key, autostart.VALUE_NAME, 0, 3, '"C:\\App\\ClipVault.exe"')))

    def test_status_reports_only_matching_current_command_as_enabled(self):
        key = mock.MagicMock()
        key.__enter__.return_value = key
        fake_winreg = types.SimpleNamespace(
            HKEY_CURRENT_USER=1,
            KEY_READ=2,
            OpenKey=lambda *_args: key,
            QueryValueEx=lambda *_args: ('"C:\\App\\ClipVault.exe"', 1),
        )

        status = autostart.status_with_registry(
            fake_winreg, '"C:\\App\\ClipVault.exe"'
        )

        self.assertTrue(status["enabled"])
        self.assertEqual(status["command"], '"C:\\App\\ClipVault.exe"')


if __name__ == "__main__":
    unittest.main()
