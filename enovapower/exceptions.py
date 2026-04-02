"""Exception classes for the Enova Power client."""


class EnovaError(Exception):
    """Base exception for Enova client errors."""


class EnovaAuthError(EnovaError):
    """Authentication failure."""


class EnovaNetworkError(EnovaError):
    """Network or connection failure."""


class EnovaSessionExpiredError(EnovaAuthError):
    """Session has expired and automatic re-login failed."""
