"""The error envelope shared by every endpoint (spec section 5)."""


class ApiError(Exception):
    def __init__(self, status, code, message=None):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code.replace("_", " ")

    def body(self):
        return {"error": {"code": self.code, "message": self.message}}


def malformed(message="request body is malformed"):
    return ApiError(400, "malformed_request", message)


def invalid(message="validation failed"):
    return ApiError(422, "validation_failed", message)


def not_found(message="not found"):
    return ApiError(404, "not_found", message)


def unauthenticated(message="missing or invalid bearer token"):
    return ApiError(401, "unauthenticated", message)
