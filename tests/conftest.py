import os
import tempfile

# Point the app at a throwaway database and upload folder BEFORE it is imported.
_tmp = tempfile.mkdtemp(prefix="geo-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:  # `with` runs the lifespan (creates tables)
        yield test_client