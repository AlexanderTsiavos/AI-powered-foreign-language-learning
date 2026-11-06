"""Safe application errors shared by HTTP and optional feature services."""


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message
        super().__init__(message)
