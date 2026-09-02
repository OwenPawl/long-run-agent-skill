"""Public error types for the epistemic compiler."""


class EpistemicError(RuntimeError):
    """Base class for user-facing epistemic compiler failures."""


class LedgerError(EpistemicError):
    """The authoritative ledger is missing, malformed, or inconsistent."""


class ConflictError(EpistemicError):
    """A write precondition no longer matches the authoritative ledger."""


class SemanticError(EpistemicError):
    """A proposed semantic operation is invalid or ambiguous."""
