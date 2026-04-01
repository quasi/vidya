"""Shared test fixtures for Vidya."""

import os
import tempfile

import pytest

from vidya.schema import init_db


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = init_db(path)
    yield conn
    conn.close()
    os.unlink(path)
