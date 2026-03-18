import configparser
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from gui_driver import create_driver

DEFAULT_CONFIG_PATH = Path.home() / ".deadline" / "config"


def _find_deadline_bin() -> str:
    if env := os.environ.get("DEADLINE_BIN"):
        return env
    candidates = [
        Path.home() / "DeadlineCloudClient" / "DeadlineClient" / "deadline",
        Path("/opt/deadline/deadline"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return subprocess.run(["which", "deadline"], capture_output=True, text=True).stdout.strip()


DEADLINE_BIN = _find_deadline_bin()


def get_config_path() -> Path:
    return Path(os.environ.get("DEADLINE_CONFIG_FILE_PATH", DEFAULT_CONFIG_PATH))


def get_config_value(key: str) -> str | None:
    config = configparser.ConfigParser()
    config.read(str(get_config_path()))
    for section in config.sections():
        if config.has_option(section, key):
            return config.get(section, key)
    return None


@pytest.fixture()
def backup_config(tmp_path):
    config_path = get_config_path()
    backup = tmp_path / "config_backup"
    if config_path.exists():
        shutil.copy2(config_path, backup)
    yield
    if backup.exists():
        shutil.copy2(backup, config_path)
    elif config_path.exists():
        config_path.unlink()


class DeadlineConfigGUI:
    def __init__(self):
        self.process = None
        self.driver = None

    def launch(self, timeout: float = 30):
        self.process = subprocess.Popen(
            [DEADLINE_BIN, "config", "gui"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.driver = create_driver(self.process.pid)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(1)
            if self.driver.window_exists():
                return
        raise RuntimeError("Config GUI did not open")

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        self.driver = None
        time.sleep(1)


@pytest.fixture()
def config_gui():
    gui = DeadlineConfigGUI()
    yield gui
    gui.close()
