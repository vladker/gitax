"""
Journal-Channel Verifier — compare journal entries against MAX channel content.

Supports all publisher types (GitHub, PyPI, Backuper, Media) with two scan modes:
  - quick:    DOM-only scan, last N messages (~30-60 seconds)
  - thorough: three-source scan (API + page state + DOM scroll)
"""

from verifier.models import (
    ChannelFile,
    ChannelRepo,
    DiffResult,
    VerifierMode,
    VersionMismatch,
)
from verifier.core import JournalChannelVerifier, VerifierError

__all__ = [
    "ChannelFile",
    "ChannelRepo",
    "DiffResult",
    "VerifierMode",
    "VersionMismatch",
    "JournalChannelVerifier",
    "VerifierError",
]
