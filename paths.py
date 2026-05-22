from pathlib import Path

APP_NAME = "Sharefolio"
APP_DATA_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
