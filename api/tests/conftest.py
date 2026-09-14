from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app, reset_warehouse


@pytest.fixture()
def client():
    reset_warehouse()
    with TestClient(app) as c:
        yield c
    reset_warehouse()
