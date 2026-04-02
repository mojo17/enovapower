"""Exception classes for the Enova Power client."""


class EnovaError(Exception):
    """Base exception for Enova client errors."""


class EnovaAuthError(EnovaError):
    """Authentication failure."""


class EnovaConnectionError(EnovaError):
    """Network or connection failure."""
