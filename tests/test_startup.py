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


def test_production_entrypoint_keeps_scheduler_independent_from_browser():
    with patch.object(run_app, "create_app", return_value="production-app") as create_app:
        with patch.object(run_app.uvicorn, "run") as run:
            run_app.main()

    create_app.assert_called_once_with(start_scheduler=True, shutdown_when_idle=False)
    run.assert_called_once_with("production-app", host=APP_HOST, port=APP_PORT)


def test_production_entrypoint_ignores_legacy_browser_managed_flag(monkeypatch):
    monkeypatch.setattr(run_app.sys, "argv", ["run_app.py", "--managed-browser"])
    with patch.object(run_app, "create_app", return_value="production-app") as create_app:
        with patch.object(run_app.uvicorn, "run"):
            run_app.main()

    create_app.assert_called_once_with(start_scheduler=True, shutdown_when_idle=False)


def test_order_reminder_launcher_prefers_chrome():
    project_root = Path(__file__).parents[1]
    launcher = (project_root / "launch_order_reminder.vbs").read_text(encoding="utf-8")

    assert "chrome.exe" in launcher.lower()
    assert "--new-window" in launcher


def test_order_reminder_launcher_reuses_service_and_registers_autostart():
    project_root = Path(__file__).parents[1]
    launcher = (project_root / "launch_order_reminder.vbs").read_text(encoding="utf-8")
    hidden_launcher = (project_root / "start_hidden.vbs").read_text(encoding="utf-8")

    assert "/api/health" in launcher
    assert 'SpecialFolders("Startup")' in launcher
    assert "OrderReminderService.lnk" in launcher
    assert "InstallAutoStart shell, pythonExe, launcher, appDir" in launcher
    assert "If Not ServiceIsRunning(browserUrl) Then" in launcher
    assert "python.exe" in launcher
    assert "Chr(34) & pythonExe & Chr(34)" in launcher
    assert 'ExpandEnvironmentStrings("%WINDIR%")' in launcher
    assert "start_hidden.vbs" in launcher
    assert "--managed-browser" not in launcher
    assert "WScript.Arguments" in hidden_launcher
    assert "python.exe" in hidden_launcher
    assert "Chr(34) & pythonExe & Chr(34)" in hidden_launcher
    assert "commandShell" not in hidden_launcher


def test_production_scheduler_has_no_browser_lifecycle_job(tmp_path):
    from app.main import create_app

    app = create_app(db_path=tmp_path / "orders.sqlite3", start_scheduler=True)

    assert {job.id for job in app.state.scheduler.get_jobs()} == {"order-reminder-check"}
