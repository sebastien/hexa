"""Module: hexa.selector — Enumerate located occurrences with enclosing captures."""

from __future__ import annotations

from .core import Failure, Gap, Issue, pointer, unescapeSegment
from .shape import SLOTS, Captures, Shape, capture, loose, mergeCaptures

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["matches"]

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def captureFields(segment: str) -> Shape:
	"""Parse a `{key:$Slot,...}` selector segment into a loose capture shape."""
	fields = {}
	for item in segment[1:-1].split(","):
		key, separator, reference = item.partition(":$")
		if not separator or reference not in SLOTS:
			raise ValueError("invalid selector capture")
		fields[key.strip()] = SLOTS[reference]
	return loose(fields)


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

def matches(value: object, selector: str) -> list[dict]:
	"""Enumerate values and JSON-pointer locations, with enclosing captures.

	`*` traverses lists; capture blocks match the current value. Missing or
	mismatched paths raise `Gap`.
	"""
	segments = selector.split("/") if selector else []
	result = []

	def visit(item, index, path, captures):
		if index == len(segments):
			entry = {"value": item, "location": pointer(path)}
			if captures:
				entry["captures"] = captures
			result.append(entry)
			return
		segment = segments[index]
		if segment.startswith("{") and segment.endswith("}"):
			try:
				inner = capture(item, captureFields(segment))
				mergeCaptures(inner, captures)
			except Failure as failure:
				if failure.kind in (Failure.Shape, Failure.Gap):
					raise Gap(failure.code, failure.path, pointer(path)) from failure
				raise
			visit(item, index + 1, path, inner)
		elif segment == "*":
			if not isinstance(item, list):
				raise Gap(Issue.Mismatch, [], pointer(path))
			for position, child in enumerate(item):
				visit(child, index + 1, (*path, position), captures.copy())
		else:
			key = unescapeSegment(segment)
			if not isinstance(item, dict):
				raise Gap(Issue.Mismatch, [], pointer(path))
			if key not in item:
				raise Gap(Issue.Missing, [key], pointer(path))
			visit(item[key], index + 1, (*path, key), captures)

	visit(value, 0, (), Captures())
	return result

# EOF
