"""Module: hexa.codec — Declare table and code codecs, optionally parameterised."""

from __future__ import annotations

from .core import Gap, freeze, register
from .shape import Codec, CodecFamily, codeNamespace, compileShape, errors

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["codec"]

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def tableFunctions(table, gaps, source, canonical, reasons):
	"""Build the parse and format functions of a table codec."""
	reverse, forward = {}, {}
	for key, sources in table.items():
		if not isinstance(key, str) or errors(key, canonical):
			raise ValueError("invalid canonical table key")
		items = sources if isinstance(sources, list) else [sources]
		if not items:
			raise ValueError("empty source alternatives")
		for item in items:
			if not isinstance(item, str) or errors(item, source) or item in reverse:
				raise ValueError("invalid or duplicate table source")
			reverse[item] = key
		forward[key] = items[0]
	for item in gaps:
		if item in reverse or not isinstance(item, str):
			raise ValueError("duplicate or invalid gap source")

	def parse(value):
		if value in gaps:
			raise Gap(getattr(reasons, gaps[value]))
		if value not in reverse:
			raise Gap(reasons.Unmapped)
		return reverse[value]

	def format(value, original=None):
		if original in reverse and reverse[original] == value:
			return original
		if value not in forward:
			raise Gap(reasons.Unformattable)
		return forward[value]

	return parse, format


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

def codec(name, *, source, canonical, parse=None, format=None, map=None, gaps=(), losses=(), params=None, examples=()):
	"""Declare a table or pure code codec, optionally parameterised."""
	source, canonical = compileShape(source), compileShape(canonical)
	if map is not None and gaps == ():
		gaps = {}
	if map is not None and not isinstance(gaps, dict):
		raise TypeError("table gaps must map source text to reason names")
	gapNames = tuple(dict.fromkeys(("Unmapped", "Unformattable", *(gaps.values() if isinstance(gaps, dict) else gaps))))
	reasons = codeNamespace(name, (*gapNames, *losses))
	if map is not None:
		if parse is not None or format is not None or params or losses:
			raise TypeError("table codec cannot also define code, parameters or losses")
		parse, format = tableFunctions(map, dict(gaps), source, canonical, reasons)
	if not callable(parse) or not callable(format):
		raise TypeError("codecs require parse and format functions")
	gapMembers = tuple(reasons.members[item] for item in gapNames)
	lossMembers = tuple(reasons.members[item] for item in losses)
	frozenExamples = tuple(freeze(item) for item in examples)
	if params:
		parameters = tuple((key, compileShape(value)) for key, value in params.items())
		return register(name, CodecFamily(name, source, canonical, parse, format, gapMembers, lossMembers, parameters, frozenExamples))
	return register(name, Codec(name, source, canonical, parse, format, gapMembers, lossMembers,
		table=freeze(map) if map is not None else None, gapTable=freeze(gaps) if isinstance(gaps, dict) else None,
		examples=frozenExamples))

# EOF
