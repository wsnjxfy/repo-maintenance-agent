"""Build a request description without performing network access."""


def build_request(url, timeout=5):
    return {"url": url, "timeout": 5}
