"""Module: hexa.core — Shared symbols, registry, failures and data-boundary checks."""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

REGISTRY = {}
MEMBERS = {}
ABSENT = object()
MAX_INT = 2**53 - 1
FAILURE_KINDS = ("Contract", "Budget", "Cancel", "Defect", "Shape", "Gap", "Effect")
LIMITS = {"maxDepth": 100, "maxNodes": 100000, "maxBytes": 10000000}


@dataclass(frozen=True, slots=True)
class Member:
	"""An interned configuration symbol, never a JSON value."""

	name: str

	@property
	def short(self):
		"""Return the member name without its namespace."""
		return self.name.rsplit(".", 1)[-1]

	def __str__(self):
		return self.name

	__repr__ = __str__

	def __deepcopy__(self, memo):
		return self


@dataclass(frozen=True, slots=True, repr=False)
class Constructor(Member):
	"""A member that also builds parameterised values, e.g. `Policy.Substitute(42)`."""

	factory: object = field(default=None, compare=False)

	def __call__(self, *arguments):
		return self.factory(*arguments)


class Namespace:
	"""Expose only explicitly declared immutable members."""

	__slots__ = ("name", "members", "unknown")

	def __init__(self, name, members, unknown=None):
		object.__setattr__(self, "name", name)
		object.__setattr__(self, "members", MappingProxyType(dict(members)))
		object.__setattr__(self, "unknown", unknown)

	def __getattr__(self, name):
		if not name.startswith("_") and name in self.members:
			return self.members[name]
		raise unknownMember(name, self.unknown or Runtime.MemberUnknown)

	def __setattr__(self, name, value):
		raise AttributeError("immutable namespace")

	def __delattr__(self, name):
		raise AttributeError("immutable namespace")

	def __eq__(self, other):
		return isinstance(other, Namespace) and self.name == other.name and dict(self.members) == dict(other.members)

	def __hash__(self):
		return hash(self.name)

	def __repr__(self):
		return self.name


class ImmutableMeta(type):
	"""Freeze declared class attributes and report unknown ones with a code."""

	def __setattr__(cls, name, value):
		raise AttributeError(f"immutable {cls.__name__}")

	def __delattr__(cls, name):
		raise AttributeError(f"immutable {cls.__name__}")

	def __getattr__(cls, name):
		raise unknownMember(name, Runtime.MemberUnknown)


# NOTE: Registry helpers precede the enums and failures declared with them.

def unknownMember(name, code):
	"""Return an `AttributeError` carrying a declared `code`."""
	error = AttributeError(name)
	error.code = code
	return error


def register(name, value):
	"""Register `value` under `name`, returning the interned value; a conflicting redeclaration is a defect."""
	existing = REGISTRY.get(name, ABSENT)
	if existing is ABSENT:
		REGISTRY[name] = value
		return value
	if existing is value or existing == value:
		return existing
	raise Failure(Failure.Defect, Runtime.NameRedeclared)


def member(name):
	if name not in MEMBERS:
		MEMBERS[name] = register(name, Member(name))
	return MEMBERS[name]


def constructor(name, factory):
	"""Declare an interned member that builds values with `factory` when called."""
	if name in MEMBERS:
		raise Failure(Failure.Defect, Runtime.NameRedeclared)
	MEMBERS[name] = register(name, Constructor(name, factory))
	return MEMBERS[name]


def namespace(name, names):
	"""Build an unregistered namespace of interned members `name.item` for each of `names`."""
	return Namespace(name, {item: member(f"{name}.{item}") for item in names})


Issue = namespace("Issue", "Mismatch Missing Unexpected Constraint CaptureConflict".split())
Runtime = namespace("Runtime", "CloseYielded MemberUnknown NameRedeclared NativeAwaitable PathSegmentInvalid ReplayMismatch SlotRedeclared SlotUnknown StageRenamed StrandUnjoined StrandConflict".split())
Ledger = namespace("Ledger", "Gap Loss".split())
Coverage = namespace("Coverage", "Supplied SourceNull NotSupplied Unmapped Conflicting".split())
Checks = namespace("Checks", "Boundary Off".split())
Profile = namespace("Profile", "Exceeded Forbidden".split())


class Failure(Exception, metaclass=ImmutableMeta):
	"""Interrupt a pipeline with value-free classification and location."""

	Contract = member("Failure.Contract")
	Budget = member("Failure.Budget")
	Cancel = member("Failure.Cancel")
	Defect = member("Failure.Defect")
	Shape = member("Failure.Shape")
	Gap = member("Failure.Gap")
	Effect = member("Failure.Effect")

	def __init__(self, kind, code, path=None, location=None):
		if kind not in FAILURE_MEMBERS:
			raise TypeError("failure kind must be a Failure member")
		if not isinstance(code, Member):
			raise TypeError("failure code must be a declared code")
		super().__init__(str(code))
		self.kind, self.code = kind, code
		self.path, self.location = path, location
		self.trace = []
		self.fatal = False
		self.exhausted = False
		self.transient = False
		self.issues = []
		self.cleanup = None
		self.ledger = None
		self.journal = None


FAILURE_MEMBERS = tuple(member(f"Failure.{kind}") for kind in FAILURE_KINDS)


class Gap(Failure):
	"""Report expected missing, unparseable or unmappable data."""

	def __init__(self, code, path=None, location=None):
		super().__init__(Failure.Gap, code, path, location)


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def jsonEqual(left, right):
	if isinstance(left, bool) or isinstance(right, bool):
		return type(left) is type(right) and left == right
	if isinstance(left, (int, float)) and isinstance(right, (int, float)):
		return left == right
	if type(left) is not type(right):
		return False
	if isinstance(left, dict):
		return left.keys() == right.keys() and all(jsonEqual(value, right[key]) for key, value in left.items())
	if isinstance(left, list):
		return len(left) == len(right) and all(jsonEqual(a, b) for a, b in zip(left, right))
	return left == right


def normalise(value):
	if isinstance(value, float) and math.isfinite(value) and value.is_integer():
		return int(value)
	if isinstance(value, list):
		return [normalise(item) for item in value]
	if isinstance(value, dict):
		return {str(key) if type(key) is int else key: normalise(item) for key, item in value.items()}
	return value


def pointer(path):
	"""Encode `path` as a JSON pointer."""
	return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in path)


def unescapeSegment(segment):
	"""Decode one JSON-pointer segment."""
	return segment.replace("~1", "/").replace("~0", "~")


def freeze(value):
	if isinstance(value, Mapping):
		return MappingProxyType({key: freeze(item) for key, item in value.items()})
	if isinstance(value, (list, tuple)):
		return tuple(freeze(item) for item in value)
	return value


def thaw(value):
	if isinstance(value, Mapping):
		return {key: thaw(item) for key, item in value.items()}
	if isinstance(value, tuple):
		return [thaw(item) for item in value]
	return value


def validateData(value, *, maxDepth=LIMITS["maxDepth"], maxNodes=LIMITS["maxNodes"], maxBytes=LIMITS["maxBytes"], allowIndexMaps=False):
	"""Reject non-JSON, cyclic, unsafe-key or oversized boundary data."""
	active = set()
	nodes = 0
	size = 0

	def visit(item, depth):
		nonlocal nodes, size
		nodes += 1
		if depth > maxDepth or nodes > maxNodes:
			raise Failure(Failure.Budget, Issue.Constraint)
		if item is None or type(item) in (bool, int):
			size += 8
		elif type(item) is float:
			if not math.isfinite(item):
				raise Failure(Failure.Contract, Issue.Mismatch)
			size += 8
		elif type(item) is str:
			try:
				size += len(item.encode("utf-8"))
			except UnicodeError:
				raise Failure(Failure.Contract, Issue.Mismatch) from None
		elif type(item) in (dict, list):
			if id(item) in active:
				raise Failure(Failure.Contract, Issue.Mismatch)
			active.add(id(item))
			if type(item) is dict:
				indexMap = allowIndexMaps and bool(item) and all(type(key) is int and key >= 0 for key in item)
				for key, child in item.items():
					if (type(key) is not str and not indexMap) or key in ("__proto__", "prototype", "constructor"):
						raise Failure(Failure.Contract, Issue.Unexpected)
					visit(key, depth + 1)
					visit(child, depth + 1)
			else:
				for child in item:
					visit(child, depth + 1)
			active.remove(id(item))
		else:
			raise Failure(Failure.Contract, Issue.Mismatch)
		if size > maxBytes:
			raise Failure(Failure.Budget, Issue.Constraint)

	visit(value, 0)


def backoff(milliseconds, *, factor=2, max=5000):
	"""Build a capped exponential delay in milliseconds."""
	if milliseconds < 0 or factor < 1 or max < 0:
		raise ValueError("invalid backoff parameters")
	def delay(attempt):
		return min(max, milliseconds * factor ** (attempt - 1))
	return delay


DEFAULT_DELAY = backoff(250)


def retryable(failure):
	"""Tell whether `failure` is a transient effect failure that may be attempted again."""
	return failure.kind is Failure.Effect and failure.transient and not failure.exhausted and not failure.fatal


def failureData(failure):
	"""Serialise `failure` as value-free journal data."""
	result = {"kind": failure.kind.short, "code": str(failure.code)}
	for key in ("path", "location", "trace"):
		value = getattr(failure, key)
		if value is not None:
			result[key] = value
	for key in ("fatal", "exhausted", "transient"):
		if getattr(failure, key):
			result[key] = True
	return result


def failureFromData(data):
	"""Restore a failure serialised by `failureData`, resolving its code through the registry."""
	try:
		failure = Failure(getattr(Failure, data["kind"]), REGISTRY[data["code"]], data.get("path"), data.get("location"))
	except (KeyError, AttributeError, TypeError):
		raise Failure(Failure.Defect, Runtime.ReplayMismatch) from None
	for key in ("transient", "fatal", "exhausted", "trace"):
		if key in data:
			setattr(failure, key, deepcopy(data[key]))
	return failure

# EOF
