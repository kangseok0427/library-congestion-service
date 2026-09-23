"""Deterministic browser-test server; never used by production entrypoints."""
import os
from datetime import datetime

from backend.app import create_app
from backend.library_hours import KST

fixed_now = datetime.fromisoformat(os.environ['E2E_DATE'] + 'T08:00:00').replace(tzinfo=KST)
app = create_app(clock=lambda: fixed_now)
