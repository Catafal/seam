"""Sample Python file for parser tests.

This file intentionally contains a variety of Python constructs:
functions, classes, methods, imports, and docstrings.
"""

import os
from pathlib import Path

CONSTANT = "hello"


def standalone_function(x: int, y: int) -> int:
    """Add two integers and return the result."""
    return x + y


def function_no_docstring(name: str) -> str:
    return f"Hello, {name}"


class SampleClass:
    """A sample class with multiple methods."""

    class_var: str = "class_var"

    def __init__(self, value: int) -> None:
        self.value = value

    def instance_method(self) -> int:
        """Return the stored value."""
        return self.value

    @staticmethod
    def static_method() -> str:
        return "static"

    @classmethod
    def class_method(cls) -> "SampleClass":
        return cls(0)


def calls_other_functions() -> int | None:
    """Calls standalone_function to demonstrate call edges."""
    result = standalone_function(1, 2)
    path = Path(os.getcwd())
    return result if path.exists() else None
