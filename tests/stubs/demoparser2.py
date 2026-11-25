"""Stand-in so the app imports where demoparser2 isn't installed (tests only)."""


class DemoParser:
    def __init__(self, *a, **k):
        raise RuntimeError("demoparser2 is not installed")
