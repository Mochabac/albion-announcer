from __future__ import annotations


class UserError(Exception):
    """An expected failure. Its message is already translated and safe to show the user."""
