from typing import Any


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: Any = None) -> None:
        self.code, self.message, self.status, self.details = code, message, status, details
        super().__init__(message)
