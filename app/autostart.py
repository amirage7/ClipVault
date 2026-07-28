"""Register ClipVault to start when the current Windows user signs in."""
import os
import subprocess
import sys


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ClipVault"


def launch_command():
    """Return a quoted command that starts this app without a console window."""
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable])
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    interpreter = pythonw if os.path.exists(pythonw) else sys.executable
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.list2cmdline([interpreter, os.path.join(root, "run.py")])


def enable_with_registry(winreg_module, command):
    open_key = getattr(winreg_module, "CreateKeyEx", winreg_module.OpenKey)
    with open_key(
        winreg_module.HKEY_CURRENT_USER,
        RUN_KEY,
        0,
        winreg_module.KEY_SET_VALUE,
    ) as key:
        winreg_module.SetValueEx(key, VALUE_NAME, 0, winreg_module.REG_SZ, command)


def enable_current_user_startup():
    if not sys.platform.startswith("win"):
        return False
    try:
        import winreg
        enable_with_registry(winreg, launch_command())
        return True
    except OSError:
        return False


def status_with_registry(winreg_module, expected_command):
    try:
        with winreg_module.OpenKey(
            winreg_module.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg_module.KEY_READ,
        ) as key:
            command, _value_type = winreg_module.QueryValueEx(key, VALUE_NAME)
    except OSError:
        command = ""
    return {
        "enabled": bool(command and command == expected_command),
        "command": command,
        "expected": expected_command,
    }


def get_status():
    if not sys.platform.startswith("win"):
        return {"enabled": False, "command": "", "expected": launch_command()}
    try:
        import winreg
        return status_with_registry(winreg, launch_command())
    except OSError:
        return {"enabled": False, "command": "", "expected": launch_command()}
