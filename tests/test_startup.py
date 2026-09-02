from pathlib import Path
from unittest.mock import patch

from app.config import APP_HOST, APP_PORT
import run_app


def test_order_reminder_uses_its_dedicated_port():
    project_root = Path(__file__).parents[1]
    launcher = (project_root / "launch_order_reminder.vbs").read_text(encoding="utf-8")

    assert APP_HOST == "127.0.0.1"
    assert APP_PORT == 8791
    assert f"http://{APP_HOST}:{APP_PORT}" in launcher


def test_production_entrypoint_enables_scheduler():
    with patch.object(run_app, "create_app", return_value="production-app") as create_app:
        with patch.object(run_app.uvicorn, "run") as run:
            run_app.main()

    create_app.assert_called_once_with(start_scheduler=True, shutdown_when_idle=True)
    run.assert_called_once_with("production-app", host=APP_HOST, port=APP_PORT)


def test_production_entrypoint_enables_browser_managed_shutdown(monkeypatch):
    monkeypatch.setattr(run_app.sys, "argv", ["run_app.py", "--managed-browser"])
    with patch.object(run_app, "create_app", return_value="production-app") as create_app:
        with patch.object(run_app.uvicorn, "run"):
            run_app.main()

    create_app.assert_called_once_with(start_scheduler=True, shutdown_when_idle=True)


def test_order_reminder_launcher_prefers_chrome():
    project_root = Path(__file__).parents[1]
    launcher = (project_root / "launch_order_reminder.vbs").read_text(encoding="utf-8")

    assert "chrome.exe" in launcher.lower()
    assert "--new-window" in launcher


def test_order_reminder_launcher_marks_service_as_browser_managed():
    project_root = Path(__file__).parents[1]
    launcher = (project_root / "launch_order_reminder.vbs").read_text(encoding="utf-8")
    hidden_launcher = (project_root / "start_hidden.vbs").read_text(encoding="utf-8")

    assert "--managed-browser" in launcher
    assert "WScript.Arguments" in hidden_launcher
