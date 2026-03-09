"""Typed exceptions for completion validation outcomes."""


class CompletionValidationError(ValueError):
    """Base class for completion validation failures."""


class CompletionSchemaError(CompletionValidationError):
    """Completion payload is invalid/missing required schema fields."""


class CompletionStaleError(CompletionValidationError):
    """Completion payload is valid schema but stale/mismatched vs active step."""

