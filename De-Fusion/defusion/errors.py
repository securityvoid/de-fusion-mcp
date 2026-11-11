"""ActionError and the error codes of design § 5.10."""

INVALID_PARAMS = "INVALID_PARAMS"
UNKNOWN_ACTION = "UNKNOWN_ACTION"
NOT_ALLOWED = "NOT_ALLOWED"
CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
NOT_FOUND = "NOT_FOUND"
AMBIGUOUS = "AMBIGUOUS"
NO_DOCUMENT = "NO_DOCUMENT"
NO_DESIGN = "NO_DESIGN"
WRONG_DESIGN_TYPE = "WRONG_DESIGN_TYPE"
UNSUPPORTED = "UNSUPPORTED"
TEXT_COMMAND_FAILED = "TEXT_COMMAND_FAILED"
NOT_EXECUTED = "NOT_EXECUTED"
RESULT_TOO_LARGE = "RESULT_TOO_LARGE"
FUSION_ERROR = "FUSION_ERROR"
INVALID_PROFILE = "INVALID_PROFILE"

CODES = frozenset(
    {
        INVALID_PARAMS, UNKNOWN_ACTION, NOT_ALLOWED, CONFIRM_REQUIRED, NOT_FOUND, AMBIGUOUS,
        NO_DOCUMENT, NO_DESIGN, WRONG_DESIGN_TYPE, UNSUPPORTED, TEXT_COMMAND_FAILED,
        NOT_EXECUTED, RESULT_TOO_LARGE, FUSION_ERROR, INVALID_PROFILE,
    }
)


class ActionError(Exception):
    """An error with a stable code; ``extra`` keywords become keys of the error object."""

    def __init__(self, code, message, hints=(), **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hints = list(hints)
        self.extra = extra

    def to_dict(self):
        return {"code": self.code, "message": self.message, "hints": list(self.hints), **self.extra}


def from_exception(exc):
    """Any exception other than ActionError becomes FUSION_ERROR with text and type name."""
    if isinstance(exc, ActionError):
        return exc
    return ActionError(FUSION_ERROR, "%s (%s)" % (exc, type(exc).__name__))
