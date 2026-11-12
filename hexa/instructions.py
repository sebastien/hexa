"""Module: hexa.instructions — Typed effect and instruction descriptions yielded by stages.

Stages yield these records to the runtime. Being typed, they never collide with plain
data: a yielded dict is always a plain value, whatever its keys.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .core import ABSENT, normalise, validateData
from .stage import asStage

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["report", "ledger", "join", "race", "fork"]


@dataclass(frozen=True, slots=True)
class Effect:
	"""An inert host operation call with JSON arguments."""

	operation: str
	arguments: list

	def encoded(self):
		return {"operation": self.operation, "arguments": deepcopy(self.arguments)}


@dataclass(frozen=True, slots=True)
class Report:
	"""A value-free gap or loss for the ledger."""

	kind: object
	target: object
	reason: object
	source: object = ABSENT
	path: object = ABSENT
	fatal: bool = False


@dataclass(frozen=True, slots=True)
class LedgerRead:
	"""A request for the current strand's ledger snapshot."""


@dataclass(frozen=True, slots=True)
class Join:
	"""Concurrent items whose results keep the input's list or struct shape."""

	items: object


@dataclass(frozen=True, slots=True)
class Race:
	"""Concurrent alternatives resolving to the first completed one."""

	items: object


@dataclass(frozen=True, slots=True)
class Fork:
	"""A concurrent stage owned by the current strand."""

	stage: object
	inputs: tuple


@dataclass(frozen=True, slots=True)
class Parallel:
	"""One forked strand per value, bounded by `maxConcurrency`, joined in order."""

	stage: object
	values: list


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

def effect(operation: str, arguments: list) -> Effect:
	"""Describe a host operation call, validating and normalising `arguments`."""
	validateData(arguments, allowIndexMaps=True)
	return Effect(operation, normalise(deepcopy(arguments)))


def report(kind, *, target, reason, source=ABSENT, path=ABSENT, fatal=False) -> Report:
	"""Describe a value-free gap or loss report."""
	if isinstance(target, str):
		raise TypeError("target must be a program entity, not text")
	return Report(kind, target, reason, source, path, fatal)


def ledger() -> LedgerRead:
	"""Describe a request for the current strand's ledger snapshot."""
	return LedgerRead()


def join(items: list | dict) -> Join:
	"""Describe concurrent work whose results retain input structure."""
	return Join(items.copy())


def race(items: list | dict) -> Race:
	"""Describe alternatives, returning only the first completed result."""
	return Race(items.copy())


def fork(stage: object, *inputs: object) -> Fork:
	"""Describe a concurrent stage owned by the current strand."""
	return Fork(asStage(stage), inputs)

# EOF
