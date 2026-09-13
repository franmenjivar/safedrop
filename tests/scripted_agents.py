"""Test-facing alias for the scripted agent stand-ins.

The implementation lives in :mod:`app.agents.scripted` so the console's
``SAFEDROP_AGENTS=scripted`` demo mode and the test suite share one definition.
"""

from app.agents.scripted import (  # noqa: F401
    contingency_model,
    diversion_model,
    elz_model,
    scripted,
)
