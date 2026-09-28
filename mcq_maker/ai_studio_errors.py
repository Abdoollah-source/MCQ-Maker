"""Safe, user-facing errors for the future Google AI Studio subsystem."""


class AIStudioError(RuntimeError):
    """Base error whose message is suitable for the MCQ Maker interface."""


class BraveNotFoundError(AIStudioError):
    pass


class InvalidBraveExecutableError(AIStudioError):
    pass


class AIStudioProfileInUseError(AIStudioError):
    pass


class AIStudioBrowserLaunchError(AIStudioError):
    pass


class AIStudioConnectionError(AIStudioError):
    pass


class AIStudioInitializationError(AIStudioError):
    pass


class AIStudioInitializationRejectedError(AIStudioInitializationError):
    """A required startup RPC completed with a non-success HTTP status."""

    def __init__(self, message, diagnostics=()):
        self.diagnostics = tuple(diagnostics)
        super().__init__(message)


class AIStudioInitializationTimeoutError(AIStudioInitializationError):
    """Required startup RPCs did not all complete before the deadline."""

    def __init__(self, message, diagnostics=()):
        self.diagnostics = tuple(diagnostics)
        super().__init__(message)


class AIStudioAuthenticationRequired(AIStudioError):
    pass


class AIStudioControlNotFoundError(AIStudioError):
    pass


class AIStudioUploadError(AIStudioError):
    pass


class AIStudioGenerationTimeoutError(AIStudioError):
    pass


class AIStudioResponseError(AIStudioError):
    pass


class AIStudioCalibrationError(AIStudioError):
    pass


class AIStudioExtractionError(AIStudioError):
    pass


class AIStudioValidationError(AIStudioError):
    pass


class AIStudioGenerationError(AIStudioError):
    pass


class AIStudioCancelledError(AIStudioError):
    pass
