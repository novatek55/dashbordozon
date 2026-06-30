from pathlib import Path

import pytest

from src.seller_price_control_sync import find_chromium_executable


def test_find_chromium_executable_prefers_explicit_path(tmp_path):
    chrome = tmp_path / "chromium"
    chrome.write_text("", encoding="utf-8")

    assert find_chromium_executable(str(chrome)) == str(chrome)


def test_find_chromium_executable_errors_when_not_found(monkeypatch):
    monkeypatch.delenv("OZON_CHROMIUM_PATH", raising=False)
    original_exists = Path.exists

    def fake_exists(self):
        if str(self) in {
            "/snap/bin/chromium",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        }:
            return False
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists)

    with pytest.raises(RuntimeError, match="Chromium/Chrome executable not found"):
        find_chromium_executable("")
