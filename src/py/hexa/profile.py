"""Module: hexa.profile — Profiles and generic request formats.

A profile is the host's allow-list for caller input (spec-004 Profiles). A request
format validates caller requests and compiles them into plans (spec-004 Requests and
plans). Decoding under a profile lives in `encoding`; the stages that apply formats
are the combinators `validate` and `plan`.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

from .core import LIMITS, NAME_RE, REGISTRY, Failure, Issue, Namespace, Profile, codeNamespace, freeze, reasonNamed, register, thaw, validateData
from .shape import (
	Codec, CodecFamily, Constraint, Shape, Slot, Tuple, check,
	compileShape, either, gather, loose, parsed, pattern, refine, rest, tolerant,
)
from .template import Transform, derive

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["ProfileDefinition", "RequestFormat", "profile", "request", "admitData", "permissionIssues", "Profile"]

# NOTE: Encoded constructor names (`$loose`, ...) by the function that builds them.
CONSTRUCTORS = {loose: "loose", Tuple: "tuple", rest: "rest", either: "either", refine: "refine", pattern: "pattern",
	tolerant: "tolerant", gather: "gather", parsed: "parsed", derive: "derive"}
CONSTRUCTOR_NAMES = {name: function for function, name in CONSTRUCTORS.items()}
BUDGET_KEYS = ("maxDepth", "maxNodes", "maxBytes")
PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


@dataclass(frozen=True, slots=True)
class ProfileDefinition:
	"""A registered allow-list of terms, request formats and budgets for caller input.

	`shapes` holds `(name, Shape)` pairs, `requests` `(RequestFormat, options)` pairs
	and `budgets` `(key, limit)` pairs; the other elements hold registered terms.
	"""

	name: str
	constructors: tuple = ()
	types: tuple = ()
	shapes: tuple = ()
	slots: tuple = ()
	constraints: tuple = ()
	derivations: tuple = ()
	codecs: tuple = ()
	requests: tuple = ()
	budgets: tuple = ()

	@property
	def limits(self):
		"""Return the effective data-only budgets, defaulting to the library limits."""
		return {**LIMITS, **dict(self.budgets)}

	def options(self, format):
		"""Return the options under which `format` is permitted, or `None`."""
		for item, options in self.requests:
			if item is format:
				return thaw(options)
		return None

	def permits(self, value):
		"""Tell whether a resolved registered term may be referenced by a caller."""
		if isinstance(value, Slot):
			namespace = value.name.rpartition(".")[0]
			return any(item is value or (isinstance(item, Namespace) and item.name == namespace) for item in self.slots)
		if isinstance(value, Shape):
			if value.kind == "type":
				return value in self.types
			return any(shape is value for _, shape in self.shapes)
		if isinstance(value, Constraint):
			return value in self.constraints
		if isinstance(value, Transform):
			return any(item is value for item in self.derivations)
		if isinstance(value, Codec):
			return any(item is value or (value.family is not None and item is value.family) for item in self.codecs)
		if isinstance(value, CodecFamily):
			return any(item is value for item in self.codecs)
		return False


@dataclass(frozen=True, slots=True, eq=False)
class RequestFormat:
	"""A registered request format: versions, an options shape, a validator and a compiler.

	`validate(options, value, catalog)` returns the normalised request or raises a
	`Failure.Contract` with issues; `compile(options, request, catalog)` returns
	`{steps, allow, gaps}` with gap codes declared by the format.
	"""

	name: str
	versions: tuple
	options: Shape
	validate: object
	compile: object
	gaps: tuple = ()

	def __getattr__(self, name):
		return reasonNamed(self.gaps, name)

	def encoded(self):
		return "$" + self.name

	def checkOptions(self, options):
		"""Check profile options for this format, raising `TypeError` or `ValueError` on a mistake."""
		if not isinstance(options, dict):
			raise TypeError("request options must be a dict")
		versions = options.get("versions")
		if not isinstance(versions, list) or not versions or any(item not in self.versions or type(item) is not int for item in versions):
			raise ValueError("request options need versions supported by the format")
		check({key: value for key, value in options.items() if key != "versions"}, self.options, Issue.Mismatch)


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def registered(value, kinds):
	"""Return `value` when it is a registered term of one of `kinds`."""
	if not isinstance(value, kinds) or REGISTRY.get(value.name) is not value:
		raise TypeError("profile elements must be registered terms")
	return value


def permissionIssues(value, definition, catalog=None):
	"""Collect `Profile.Forbidden` issues for every term the profile does not permit.

	Unknown and forbidden names are refused alike, so refusals never disclose the
	registry. Malformed operators are left to construction, which rejects them.
	"""
	catalog = REGISTRY if catalog is None else catalog
	issues = []

	def refuse(path):
		issues.append({"code": Profile.Forbidden, "path": list(path)})

	def name(item, path):
		if not isinstance(item, str) or not definition.permits(catalog.get(item)):
			refuse(path)

	def codecReference(item, path):
		if isinstance(item, dict) and "$" in item:
			name(item["$"], path)
		else:
			name(item, path)

	def patternNames(source, path):
		if not isinstance(source, str):
			return
		for found in PLACEHOLDER_RE.finditer(source):
			slotName, separator, codecName = found.group(1).partition(":")
			if "." in slotName or slotName in ("Location", "Origin"):
				name(slotName, path)
			if separator:
				name(codecName, path)

	def visit(item, path):
		if isinstance(item, str):
			if item.startswith("$") and not item.startswith("$$"):
				name(item[1:], path)
			return
		if isinstance(item, list):
			for index, child in enumerate(item):
				visit(child, (*path, index))
			return
		if not isinstance(item, dict):
			return
		operators = [key for key in item if key.startswith("$") and not key.startswith("$$")]
		if not operators:
			if set(item) == {"operation", "arguments"}:
				refuse(path)
				return
			for key, child in item.items():
				visit(child, (*path, key))
			return
		if len(operators) != 1:
			return
		operator = operators[0]
		if operator == "$":
			if "value" in item or "stage" in item:
				refuse(path)
				return
			name(item["$"], path)
			if "via" in item:
				codecReference(item["via"], (*path, "via"))
			return
		if operator[1:] not in definition.constructors:
			refuse((*path, operator))
			return
		if operator == "$pattern":
			patternNames(item[operator], (*path, operator))
			return
		visit(item[operator], (*path, operator))
		for option in ("constraints", "arguments"):
			if option in item:
				visit(item[option], (*path, option))

	visit(value, ())
	return issues


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

def request(name, *, versions, options, validate, compile, gaps=()):
	"""Register a request format and declare its gap codes as `<Name>.<Reason>`."""
	if not NAME_RE.fullmatch(name) or not versions or any(type(item) is not int for item in versions):
		raise ValueError("invalid request format")
	if not callable(validate) or not callable(compile):
		raise TypeError("request formats require validate and compile functions")
	reasons = codeNamespace(name, gaps)
	return register(name, RequestFormat(name, tuple(versions), compileShape(options), validate, compile, tuple(reasons.members.values())))


def profile(name, *, constructors=(), types=(), shapes=None, slots=(), constraints=(), derivations=(), codecs=(), requests=None, budgets=None):
	"""Register a profile: the terms, request formats and budgets permitted to callers.

	`shapes` maps names to shapes and registers each under its name, so callers
	reference it as `$Name`.
	"""
	if not NAME_RE.fullmatch(name):
		raise ValueError("invalid profile name")
	if any(item not in CONSTRUCTORS for item in constructors):
		raise TypeError("constructors must be shape constructors")
	for item in types:
		if not isinstance(item, Shape) or item.kind != "type":
			raise TypeError("types must be library types")
	named = []
	for key, shape in (shapes or {}).items():
		if not NAME_RE.fullmatch(key) or "." in key:
			raise ValueError("invalid shape name")
		named.append((key, register(key, compileShape(shape))))
	for item in slots:
		if isinstance(item, Namespace):
			if REGISTRY.get(item.name) is not item or not all(isinstance(child, Slot) for child in item.members.values()):
				raise TypeError("slots must be slots or slot namespaces")
		else:
			registered(item, Slot)
	for item in constraints:
		registered(item, Constraint)
	for item in derivations:
		registered(item, Transform)
	for item in codecs:
		registered(item, (Codec, CodecFamily))
	formats = []
	for format, options in (requests or {}).items():
		registered(format, RequestFormat)
		format.checkOptions(options)
		formats.append((format, freeze(deepcopy(options))))
	budgets = dict(budgets or {})
	for key, limit in budgets.items():
		if key not in BUDGET_KEYS or type(limit) is not int or not 1 <= limit <= LIMITS[key]:
			raise ValueError("profile budgets must be within the library limits")
	return register(name, ProfileDefinition(name, tuple(CONSTRUCTORS[item] for item in constructors), tuple(types), tuple(named),
		tuple(slots), tuple(constraints), tuple(derivations), tuple(codecs), tuple(formats), tuple(budgets.items())))


def admitData(value, definition):
	"""Check caller data is JSON within the profile's budgets; an overrun is `Profile.Exceeded`."""
	try:
		validateData(value, **definition.limits)
	except Failure as failure:
		if failure.kind is Failure.Budget:
			raise Failure(Failure.Budget, Profile.Exceeded) from None
		raise


# EOF
