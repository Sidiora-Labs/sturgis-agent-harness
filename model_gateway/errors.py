"""Sanitized errors shared by the independent gateway boundaries."""


class GatewayError(Exception):
    """A safe public error; upstream bodies and credentials never belong here."""

    def __init__(self, code, message, status=400, retryable=False, **details):
        super().__init__(message)
        self.code, self.message = code, message
        self.status, self.retryable, self.details = status, retryable, details

    def as_dict(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}
