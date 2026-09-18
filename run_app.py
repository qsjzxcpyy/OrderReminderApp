from pathlib import Path
import argparse
import sys

import uvicorn

from app.config import APP_HOST, APP_PORT
from app.main import create_app


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--managed-browser", action="store_true")
    args, _unknown = parser.parse_known_args()
    app_dir = Path(__file__).resolve().parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    application = create_app(start_scheduler=True, shutdown_when_idle=False)
    uvicorn.run(application, host=APP_HOST, port=APP_PORT)


if __name__ == "__main__":
    main()
