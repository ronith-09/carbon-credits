"""Mock solar API transport. Returns exact response bytes, not a reserialization."""
from pathlib import Path

class MockSolarAPI:
    provider = "mock-solar"

    def fetch(self, fixture):
        return Path(fixture).read_bytes()
