"""Compatibility exports for the SSH authentication layer."""

from .auth import AutoAuthProvider, AuthenticationCancelled, ManualAuthProvider, make_provider

__all__ = ["AutoAuthProvider", "AuthenticationCancelled", "ManualAuthProvider", "make_provider"]
