import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'geo.db'}")

# Upload limits (the API processes files synchronously, so we cap the work).
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per uploaded file
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # zip-bomb guard
MAX_FEATURES = 50_000
ALLOWED_EXTENSIONS = {".kml", ".zip"}