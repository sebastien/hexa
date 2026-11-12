"""Module: hexa.shapes — Match, capture, convert and reproject JSON data.

Public façade over `shape`, `template`, `codec`, `selector`, `encoding` and
`vocabulary`; import canonical names from here.
"""

from __future__ import annotations

from .codec import codec
from .core import Failure, Gap, Issue
from .encoding import decode, encode
from .selector import matches
from .shape import (
	Any, Bool, Decimal, DateTime, Float, Instant, Int, List, Null, Num, Struct, Text, Tuple, __,
	capture, check, codes, either, errors, evidence, gather, lossy, loose, nullable, optional,
	parsed, pattern, refine, rest, slot, slots, tolerant, via, where,
)
from .template import derive, expand, reshape, transform
from .vocabulary import DateOnly, DatePattern, DecimalText, InstantOffset, Location, Nonempty, Origin, Positive, Timed, Zoned, parts

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = [
	"Any", "Text", "Num", "Int", "Float", "Bool", "Null", "Struct", "List", "DateTime",
	"Instant", "Decimal", "Tuple", "rest", "__", "loose", "either", "refine", "where",
	"pattern", "nullable", "tolerant", "gather", "slots", "slot", "via", "optional",
	"derive", "codec", "transform", "lossy", "codes", "Location", "Origin", "DateOnly",
	"Timed", "Zoned", "Positive", "Nonempty", "parts", "InstantOffset", "DecimalText",
	"DatePattern", "errors", "check", "capture", "expand", "reshape", "matches", "evidence",
	"parsed", "encode", "decode", "Failure", "Gap", "Issue"
]

# EOF
