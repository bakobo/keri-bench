"""The harness's one error type: a stable code, then plain sentences (dev/standards/error-codes.md).

Codes are declared as module-scope literals beside the code that raises them, so a reader finds
every code a module can raise at its top.
"""


class BenchError(Exception):
    """A refusal or failure carrying a code whose trailing token says whether a retry can help."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
