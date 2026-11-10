class ProcessingError(Exception):
    """Base class. `retryable` controls whether the job is retried."""
    retryable = False


class UnsupportedFileError(ProcessingError):
    pass


class CorruptFileError(ProcessingError):
    pass


class TransientError(ProcessingError):
    retryable = True
