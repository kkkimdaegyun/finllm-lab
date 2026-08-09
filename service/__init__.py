"""Production serving layer for FinLLM Lab v0.2.

The service deliberately imports the existing retrieval and prompt builders
instead of carrying a second implementation of the evaluation semantics.
"""

__all__ = ["__version__"]

__version__ = "0.2.0"
