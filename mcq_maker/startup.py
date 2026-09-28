"""Per-user Windows sign-in startup registration."""
from pathlib import Path
import subprocess
import sys


RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'MCQ Maker'


class StartupRegistrationError(OSError):
    pass


class WindowsStartupManager:
    def __init__(self, registry=None, executable=None, source_launcher=None):
        if registry is None:
            import winreg
            registry = winreg
        self.registry = registry
        self.executable = Path(executable or sys.executable)
        self.source_launcher = Path(source_launcher or Path(__file__).resolve().parents[1] / 'launch_mcq_maker.pyw')

    def command(self):
        if getattr(sys, 'frozen', False):
            parts = [str(self.executable)]
        else:
            pythonw = self.executable.with_name('pythonw.exe')
            parts = [str(pythonw if pythonw.exists() else self.executable), str(self.source_launcher)]
        return subprocess.list2cmdline(parts)

    def set_enabled(self, enabled):
        try:
            with self.registry.CreateKeyEx(
                self.registry.HKEY_CURRENT_USER, RUN_KEY, 0, self.registry.KEY_SET_VALUE
            ) as key:
                if enabled:
                    self.registry.SetValueEx(key, VALUE_NAME, 0, self.registry.REG_SZ, self.command())
                else:
                    try:
                        self.registry.DeleteValue(key, VALUE_NAME)
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            action = 'enable' if enabled else 'disable'
            raise StartupRegistrationError(f'MCQ Maker could not {action} Start with Windows.') from exc
