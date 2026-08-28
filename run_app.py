from pathlib import Path
import sys

import uvicorn

from app.config import APP_HOST, APP_PORT


if __name__ == "__main__":
    app_dir = Path(__file__).resolve().parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    uvicorn.run("app.main:create_app", factory=True, host=APP_HOST, port=APP_PORT)
