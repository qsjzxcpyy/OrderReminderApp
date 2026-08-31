from pathlib import Path

from app.config import APP_HOST, APP_PORT


def test_order_reminder_uses_its_dedicated_port():
    project_root = Path(__file__).parents[1]
    launcher = (project_root / "launch_order_reminder.vbs").read_text(encoding="utf-8")

    assert APP_HOST == "127.0.0.1"
    assert APP_PORT == 8791
    assert f"http://{APP_HOST}:{APP_PORT}" in launcher
