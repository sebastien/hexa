"""Module: hexa.shape — Shape algebra: types, constructors, validation and matching.

Matching, validation and codec conversion are mutually recursive (a codec checks its
values with `errors`, `compatible` matches literals), so they share this module.
"""

from __future__ import annotations

import inspect
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date

from .core import (
	MAX_INT, REGISTRY, Failure, Gap, Issue, Member, Namespace, Runtime,
	freeze, jsonEqual, member, normalise, pointer, register, thaw, unknownMember, validateData,
)

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = [
	"Any", "Text", "Num", "Int", "Float", "Bool", "Null", "Struct", "List", "DateTime",
	"Instant", "Decimal", "Tuple", "rest", "__", "loose", "either", "nullable", "where",
	"refine", "via", "optional", "tolerant", "gather", "parsed", "pattern", "lossy",
	"codes", "slot", "slots", "errors", "check", "capture", "evidence"
]

SLOTS = {}
DATE_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?:T([0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?)(Z|[+-][0-9]{2}:[0-9]{2})?)?")
INSTANT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{0,8}[1-9])?Z")
DECIMAL_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?")
NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*")


@dataclass(frozen=True, slots=True, eq=False)
class Shape:
	"""An immutable compiled shape or template operator.

	`args` by `kind`:

	| kind | args |
	| --- | --- |
	| `type` | `(name,)`, e.g. `("Text",)` |
	| `literal` | `(value,)` |
	| `struct`, `loose` | `((key, optional, Shape), ...)` |
	| `list`, `rest`, `tolerant`, `gather` | `(item,)` |
	| `tuple`, `either` | `(Shape, ...)` |
	| `refine` | `(Shape, (Constraint, ...))` |
	| `slot`, `optional`, `parsed` | `(Slot,)` |
	| `via` | `(Slot, Codec)` |
	| `pattern` | `(source, regex, ((Slot or local name, Codec or None), ...))` |
	| `derive` (template only) | `(Transform, (Shape, ...))` |
	"""

	kind: str
	args: tuple = ()

	def __eq__(self, other):
		return isinstance(other, Shape) and self.kind == other.kind and sameArguments(self.args, other.args)

	def __hash__(self):
		return hash((self.kind, self.args))

	def __and__(self, constraint):
		return refine(self, constraint)


@dataclass(frozen=True, slots=True)
class Slot:
	"""A process-interned typed capture point."""

	name: str
	shape: Shape

	def __matmul__(self, codec):
		return via(self, codec)

	def __invert__(self):
		return optional(self)

	def __and__(self, constraint):
		return refine(self, constraint)

	def __repr__(self):
		return self.name

	def __deepcopy__(self, memo):
		return self


@dataclass(frozen=True, slots=True)
class Constraint:
	"""A named pure predicate applied after structural matching."""

	name: str
	predicate: object = field(compare=False, repr=False)

	def __rand__(self, shape):
		return refine(shape, self)


@dataclass(frozen=True, slots=True)
class Lossy:
	"""A codec result whose conversion discarded information for a declared `reason`."""

	value: object
	reason: Member


class Captures(dict):
	"""Capture values with private evidence, failed references and losses."""

	def __init__(self):
		super().__init__()
		self.evidence = {}
		self.failed = {}
		self.losses = []
		self.gathered = set()

	def copy(self):
		result = Captures()
		result.update(self)
		result.evidence = deepcopy(self.evidence)
		result.failed = dict(self.failed)
		result.losses = list(self.losses)
		result.gathered = set(self.gathered)
		return result

	def replace(self, other):
		self.clear()
		self.update(other)
		self.evidence, self.failed = other.evidence, other.failed
		self.losses, self.gathered = other.losses, other.gathered


@dataclass(frozen=True, slots=True, eq=False)
class Codec:
	"""A concrete codec converting `source` values to `canonical` ones and back.

	An instance of a `CodecFamily` carries the family and its `arguments`.
	"""

	name: str
	source: Shape
	canonical: Shape
	parse: object = field(repr=False)
	format: object = field(repr=False)
	gaps: tuple = ()
	losses: tuple = ()
	table: object = None
	gapTable: object = None
	examples: tuple = ()
	arguments: tuple = ()
	family: object = None

	def __getattr__(self, name):
		return reasonNamed(self.gaps + self.losses, name)

	def convert(self, value, *, formatting=False, original=None):
		expected = self.canonical if formatting else self.source
		if errors(value, expected):
			raise Failure(Failure.Defect, Issue.Mismatch)
		try:
			if formatting:
				result = self.format(*thaw(self.arguments), deepcopy(value), original=deepcopy(original))
			else:
				result = self.parse(*thaw(self.arguments), deepcopy(value))
		except Gap as failure:
			if failure.code not in self.gaps:
				raise Failure(Failure.Defect, Issue.Unexpected) from failure
			raise
		except Failure as failure:
			raise Failure(Failure.Defect, Issue.Mismatch) from failure
		except Exception as error:
			raise Failure(Failure.Defect, Issue.Mismatch) from error
		actual = result.value if isinstance(result, Lossy) else result
		if isinstance(result, Lossy) and (formatting or result.reason not in self.losses):
			raise Failure(Failure.Defect, Issue.Unexpected)
		checkOutput(actual)
		if errors(actual, self.source if formatting else self.canonical):
			raise Failure(Failure.Defect, Issue.Mismatch)
		return result


@dataclass(frozen=True, slots=True, eq=False)
class CodecFamily:
	"""A parameterised codec: calling it with arguments returns a cached `Codec`."""

	name: str
	source: Shape
	canonical: Shape
	parse: object = field(repr=False)
	format: object = field(repr=False)
	gaps: tuple = ()
	losses: tuple = ()
	params: tuple = ()
	examples: tuple = ()
	instances: dict = field(default_factory=dict, repr=False)

	def __getattr__(self, name):
		return reasonNamed(self.gaps + self.losses, name)

	def __call__(self, *arguments):
		if len(arguments) != len(self.params):
			raise TypeError("codec arguments do not match parameters")
		for value, (_, shape) in zip(arguments, self.params):
			validateData(value)
			check(value, shape, Issue.Mismatch)
		key = json.dumps(arguments, separators=(",", ":"), ensure_ascii=False)
		if key not in self.instances:
			name = self.name + "(" + key[1:-1] + ")"
			self.instances[key] = register(name, Codec(name, self.source, self.canonical, self.parse, self.format, self.gaps, self.losses,
				examples=self.examples, arguments=tuple(freeze(value) for value in arguments), family=self))
		return self.instances[key]


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def sameArguments(left, right):
	"""Compare shape arguments with JSON semantics: booleans never equal numbers."""
	if isinstance(left, tuple) and isinstance(right, tuple):
		return len(left) == len(right) and all(sameArguments(a, b) for a, b in zip(left, right))
	if isinstance(left, bool) or isinstance(right, bool):
		return type(left) is type(right) and left == right
	return left == right


def reasonNamed(reasons, name):
	"""Return the member of `reasons` whose short name is `name`, as a codec or transform attribute."""
	if not name.startswith("_"):
		for item in reasons:
			if item.short == name:
				return item
	raise unknownMember(name, Runtime.MemberUnknown)


def asCaptures(captures):
	"""Return `captures` as `Captures`, wrapping a plain mapping without evidence."""
	if isinstance(captures, Captures):
		return captures
	result = Captures()
	result.update(captures)
	return result


def checkOutput(value):
	"""Reject awaitables and non-JSON results from user-supplied functions."""
	if inspect.isawaitable(value):
		if inspect.iscoroutine(value):
			value.close()
		raise Failure(Failure.Defect, Runtime.NativeAwaitable)
	try:
		validateData(value)
	except Failure as failure:
		raise Failure(Failure.Defect, failure.code) from failure


def compatible(source, target):
	"""Check the structural subset needed for static codec/derivation signatures."""
	if target == Any or source == target:
		return True
	if source.kind == "literal":
		return not errors(source.args[0], target)
	if source.kind == "either":
		return all(compatible(item, target) for item in source.args)
	if target.kind == "either":
		return any(compatible(source, item) for item in target.args)
	if target.kind == "refine":
		return source.kind == "refine" and compatible(source.args[0], target.args[0]) and set(target.args[1]) <= set(source.args[1])
	if source.kind == "refine":
		return compatible(source.args[0], target)
	if source.kind == target.kind == "type":
		return (source.args[0] in ("Int", "Float") and target.args[0] == "Num") or (source.args[0] == "Instant" and target.args[0] == "DateTime")
	if source.kind == target.kind == "list":
		return compatible(source.args[0], target.args[0])
	if source.kind in ("struct", "loose") and target.kind in ("struct", "loose"):
		fields = {key: (optionalKey, child) for key, optionalKey, child in source.args}
		for key, optionalKey, child in target.args:
			if key not in fields:
				if not optionalKey:
					return False
			elif (fields[key][0] and not optionalKey) or not compatible(fields[key][1], child):
				return False
		return target.kind == "loose" or (source.kind == "struct" and set(fields) <= {key for key, _, _ in target.args})
	return False


def dateParts(value):
	if not isinstance(value, str) or not (found := DATE_RE.fullmatch(value)):
		return None
	dateText, timeText, zone = found.groups()
	try:
		date(*map(int, dateText.split("-")))
	except ValueError:
		return None
	if timeText:
		hour, minute = map(int, timeText[:5].split(":"))
		if hour > 23 or minute > 59 or (len(timeText) > 5 and int(timeText[6:8]) > 59):
			return None
	if zone and zone != "Z" and (int(zone[1:3]) > 23 or int(zone[4:]) > 59):
		return None
	return {"date": dateText, "time": timeText, "zone": zone}


def typeMatches(value, name):
	if name == "Any":
		return True
	if name == "Text":
		return type(value) is str
	if name == "Bool":
		return type(value) is bool
	if name == "Null":
		return value is None
	if name == "Struct":
		return type(value) is dict and all(type(key) is str for key in value)
	if name == "List":
		return type(value) is list
	if name in ("Num", "Int", "Float"):
		if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
			return False
		integral = type(value) is int or value.is_integer()
		return name == "Num" or (name == "Int" and integral and abs(value) <= MAX_INT) or (name == "Float" and not integral)
	if name == "DateTime":
		return dateParts(value) is not None
	if name == "Instant":
		return dateParts(value) is not None and INSTANT_RE.fullmatch(value) is not None
	if name == "Decimal":
		return isinstance(value, str) and value != "-0" and DECIMAL_RE.fullmatch(value) is not None
	return False


def isCollectionSlot(shape):
	"""Tell whether `shape` is a slot (or codec slot) whose own shape is a list."""
	return shape.kind in ("slot", "via") and shape.args[0].shape.kind == "list"


def compileShape(value):
	if isinstance(value, Shape):
		return value
	if isinstance(value, Slot):
		return Shape("slot", (value,))
	for native, shape in ((str, Text), (int, Int), (float, Num), (bool, Bool), (dict, Struct), (list, List)):
		if value is native:
			return shape
	if isinstance(value, dict):
		if not all(isinstance(key, str) for key in value):
			raise TypeError("shape keys must be strings")
		return Shape("struct", tuple((key[:-1] if key.endswith("?") else key, key.endswith("?"), compileShape(item)) for key, item in value.items()))
	if isinstance(value, list):
		if len(value) != 1:
			raise TypeError("list shapes have exactly one item; use Tuple")
		item = compileShape(value[0])
		return Shape("list", (Shape("gather", (item,)) if isCollectionSlot(item) else item,))
	if value is None or type(value) in (str, int, float, bool):
		validateData(value)
		return Shape("literal", (value,))
	raise TypeError("unsupported shape")


def children(shape):
	if shape.kind in ("struct", "loose"):
		return [item[2] for item in shape.args]
	if shape.kind in ("tuple", "either"):
		return list(shape.args)
	if shape.kind in ("list", "rest", "tolerant", "gather", "refine"):
		return [shape.args[0]]
	return []


def references(shape):
	result = set()
	if shape.kind in ("slot", "via", "optional", "parsed"):
		result.add(shape.args[0])
	if shape.kind == "pattern":
		result.update(item[0] for item in shape.args[2])
	if shape.kind == "derive":
		for argument in shape.args[1]:
			result.update(references(argument))
	for child in children(shape):
		result.update(references(child))
	return result


def definiteReferences(shape):
	if shape.kind == "either":
		return set.intersection(*(definiteReferences(child) for child in shape.args))
	if shape.kind in ("slot", "via", "pattern"):
		return references(shape)
	result = set()
	for child in children(shape):
		result.update(definiteReferences(child))
	return result


def gatherReferences(shape):
	if shape.kind == "gather":
		return {shape.args[0].args[0]}
	result = set()
	for child in children(shape):
		result.update(gatherReferences(child))
	return result


def validateShape(shape, inList=False, gathers=None, allowRest=False, itemMode=False):
	gathers = {} if gathers is None else gathers
	if shape.kind in ("optional", "derive", "parsed"):
		raise TypeError("template operator used in a shape")
	if shape.kind == "rest" and not allowRest:
		raise TypeError("rest is valid only directly inside Tuple")
	if shape.kind == "rest" and isCollectionSlot(shape.args[0]):
		key = shape.args[0].args[0]
		if key in gathers:
			raise TypeError("a collection slot can be gathered at only one position")
		gathers[key] = True
	if shape.kind == "via":
		expected = shape.args[0].shape
		if itemMode and expected.kind == "list":
			expected = expected.args[0]
		if not compatible(shape.args[1].canonical, expected):
			raise TypeError("codec canonical shape is incompatible with slot")
	if shape.kind == "gather":
		if not inList:
			raise TypeError("gather requires an enclosing list")
		key = shape.args[0].args[0]
		if key in gathers:
			raise TypeError("a collection slot can be gathered at only one position")
		gathers[key] = True
	if shape.kind == "tuple" and sum(item.kind == "rest" for item in shape.args) > 1:
		raise TypeError("Tuple allows at most one rest")
	for child in children(shape):
		validateShape(child, inList or shape.kind in ("list", "rest"), gathers, shape.kind == "tuple", shape.kind in ("gather", "rest"))


def mergeCaptures(target, source):
	for key, value in source.items():
		if key in target and not jsonEqual(target[key], value):
			raise Failure(Failure.Shape, Issue.CaptureConflict)
		if key not in target:
			target[key] = deepcopy(value)
			if key in source.evidence:
				target.evidence[key] = deepcopy(source.evidence[key])
	target.failed.update({key: reason for key, reason in source.failed.items() if key not in target})
	target.losses.extend(source.losses)
	return target


def startGather(captures, key):
	"""Reset collection slot `key` to an empty capture with empty evidence."""
	captures[key] = []
	captures.gathered.add(key)
	captures.evidence[key] = {"raw": [], "location": [], "codec": []}


def addIssue(issues, code, path, **extra):
	issues.append({"code": code, "path": list(path), **extra})


def bindCapture(captures, key, value, raw, path, issues, codec=None, gathering=False):
	if gathering and key in captures.failed:
		return
	value = normalise(value)
	if gathering:
		captures.setdefault(key, []).append(value)
		entry = captures.evidence.setdefault(key, {"raw": [], "location": [], "codec": []})
		entry["raw"].append(deepcopy(raw))
		entry["location"].append(pointer(path))
		entry["codec"].append(codec.name if codec else None)
	elif key in captures and not jsonEqual(captures[key], value):
		addIssue(issues, Issue.CaptureConflict, path)
	else:
		itemEvidence = {"raw": deepcopy(raw), "location": pointer(path)}
		if codec is not None:
			itemEvidence["codec"] = codec.name
		captures.setdefault(key, value)
		captures.evidence.setdefault(key, itemEvidence)


# NOTE: Matchers share one signature so `matchShape` can dispatch on `shape.kind`.

def matchType(value, shape, captures, issues, path, gathering):
	if not typeMatches(value, shape.args[0]):
		addIssue(issues, Issue.Mismatch, path)


def matchLiteral(value, shape, captures, issues, path, gathering):
	if not jsonEqual(value, shape.args[0]):
		addIssue(issues, Issue.Mismatch, path)


def matchStruct(value, shape, captures, issues, path, gathering):
	if not typeMatches(value, "Struct"):
		addIssue(issues, Issue.Mismatch, path)
		return
	for key, isOptional, child in shape.args:
		if key not in value:
			if not isOptional:
				addIssue(issues, Issue.Missing, (*path, key))
		else:
			matchShape(value[key], child, captures, issues, (*path, key))
	if shape.kind == "struct":
		keys = {item[0] for item in shape.args}
		for key in value:
			if key not in keys:
				addIssue(issues, Issue.Unexpected, (*path, key))


def gatherItem(shape):
	"""Return a rest or list item shape, wrapping a collection slot so it gathers."""
	return Shape("gather", (shape,)) if isCollectionSlot(shape) else shape


def matchList(value, shape, captures, issues, path, gathering):
	if type(value) is not list:
		addIssue(issues, Issue.Mismatch, path)
		return
	child = gatherItem(shape.args[0]) if shape.kind == "rest" else shape.args[0]
	for key in gatherReferences(child):
		if key not in captures.gathered:
			startGather(captures, key)
	for index, item in enumerate(value):
		matchShape(item, child, captures, issues, (*path, index))


def matchTuple(value, shape, captures, issues, path, gathering):
	args = shape.args
	if type(value) is not list:
		addIssue(issues, Issue.Mismatch, path)
		return
	restIndex = next((i for i, item in enumerate(args) if item.kind == "rest"), None)
	if (restIndex is None and len(value) != len(args)) or (restIndex is not None and len(value) < len(args) - 1):
		addIssue(issues, Issue.Mismatch, path)
		return
	for index, child in enumerate(args):
		if index == restIndex:
			# Match each rest item at its original tuple index, including evidence.
			itemShape = gatherItem(child.args[0])
			for key in gatherReferences(itemShape):
				startGather(captures, key)
			for offset in range(index, len(value) - (len(args) - index - 1)):
				matchShape(value[offset], itemShape, captures, issues, (*path, offset))
		else:
			position = index if restIndex is None or index < restIndex else len(value) - len(args) + index
			matchShape(value[position], child, captures, issues, (*path, position))


def matchEither(value, shape, captures, issues, path, gathering):
	branchIssues = []
	for child in shape.args:
		trial, childIssues = captures.copy(), []
		try:
			matchShape(value, child, trial, childIssues, path)
		except Gap as failure:
			branchIssues.append(failure)
			continue
		if not childIssues:
			captures.replace(trial)
			return
		branchIssues.append(childIssues)
	if branchIssues and all(isinstance(item, Gap) for item in branchIssues):
		raise branchIssues[0]
	addIssue(issues, Issue.Mismatch, path)


def matchRefine(value, shape, captures, issues, path, gathering):
	before = len(issues)
	matchShape(value, shape.args[0], captures, issues, path)
	if len(issues) != before:
		return
	for constraint in shape.args[1]:
		try:
			valid = constraint.predicate(value)
		except Exception as error:
			raise Failure(Failure.Defect, Issue.Constraint, list(path)) from error
		if inspect.isawaitable(valid):
			if inspect.iscoroutine(valid):
				valid.close()
			raise Failure(Failure.Defect, Runtime.NativeAwaitable)
		if not valid:
			addIssue(issues, Issue.Constraint, path, constraint=constraint.name)
			break


def matchTolerant(value, shape, captures, issues, path, gathering):
	trial, childIssues = captures.copy(), []
	try:
		matchShape(value, shape.args[0], trial, childIssues, path)
		reason = childIssues[0]["code"] if childIssues else None
	except Gap as failure:
		reason = failure.code
	if not reason:
		captures.replace(trial)
		return
	for key in references(shape.args[0]):
		if key not in captures or key in captures.gathered:
			captures.failed[key] = reason
			captures.pop(key, None)
			captures.evidence.pop(key, None)
			captures.losses = [loss for loss in captures.losses if loss["target"] != getattr(key, "name", key)]


def matchGather(value, shape, captures, issues, path, gathering):
	matchShape(value, shape.args[0], captures, issues, path, gathering=True)


def matchSlot(value, shape, captures, issues, path, gathering):
	key = shape.args[0]
	expected = key.shape.args[0] if gathering else key.shape
	codecValue = shape.args[1] if shape.kind == "via" else None
	before = len(issues)
	matchShape(value, codecValue.source if codecValue else expected, captures, issues, path)
	if len(issues) != before:
		return
	canonical = value
	if codecValue:
		try:
			canonical = codecValue.convert(value)
		except Gap as failure:
			failure.path = list(path)
			raise
		if isinstance(canonical, Lossy):
			captures.losses.append({"target": key.name if isinstance(key, Slot) else key, "reason": canonical.reason, "path": list(path)})
			canonical = canonical.value
		matchShape(canonical, expected, captures, issues, path)
	if len(issues) == before:
		bindCapture(captures, key, canonical, value, path, issues, codecValue, gathering)


def matchPattern(value, shape, captures, issues, path, gathering):
	if not isinstance(value, str) or not (found := shape.args[1].fullmatch(value)):
		addIssue(issues, Issue.Mismatch, path)
		return
	for index, (key, codecValue) in enumerate(shape.args[2]):
		raw = found.group(index + 1)
		if isinstance(key, Slot):
			matchShape(raw, via(key, codecValue) if codecValue else compileShape(key), captures, issues, path)
			continue
		canonical = codecValue.convert(raw) if codecValue else raw
		if isinstance(canonical, Lossy):
			captures.losses.append({"target": key, "reason": canonical.reason, "path": list(path)})
			canonical = canonical.value
		bindCapture(captures, key, canonical, raw, path, issues, codecValue)


MATCHERS = {
	"type": matchType, "literal": matchLiteral, "struct": matchStruct, "loose": matchStruct,
	"list": matchList, "rest": matchList, "tuple": matchTuple, "either": matchEither,
	"refine": matchRefine, "tolerant": matchTolerant, "gather": matchGather,
	"slot": matchSlot, "via": matchSlot, "pattern": matchPattern,
}


def matchShape(value, shape, captures, issues, path=(), *, gathering=False):
	"""Match `value` against `shape`, binding `captures` and appending value-free `issues`."""
	matcher = MATCHERS.get(shape.kind)
	if matcher is None:
		raise TypeError("invalid matching operator")
	matcher(value, shape, captures, issues, path, gathering)


def codeNamespace(name, names):
	"""Build an unregistered namespace of interned codes under `name`."""
	if not NAME_RE.fullmatch(name) or any(not NAME_RE.fullmatch(item) or "." in item for item in names):
		raise ValueError("invalid code name")
	return Namespace(name, {item: member(f"{name}.{item}") for item in names})


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

Any, Text, Num, Int, Float, Bool, Null, Struct, List, DateTime, Instant, Decimal = (
	register(name, Shape("type", (name,))) for name in "Any Text Num Int Float Bool Null Struct List DateTime Instant Decimal".split()
)


def codes(name: str, *names: str) -> Namespace:
	"""Declare interned reasons under `name`."""
	return register(name, codeNamespace(name, names))


def slot(name: str, shape: object) -> Slot:
	"""Declare one canonical capture point, rejecting incompatible redeclarations."""
	if not NAME_RE.fullmatch(name):
		raise ValueError("invalid slot name")
	compiled = compileShape(shape)
	validateShape(compiled)
	if name in SLOTS:
		if SLOTS[name].shape != compiled:
			raise Failure(Failure.Defect, Runtime.SlotRedeclared)
		return SLOTS[name]
	result = register(name, Slot(name, compiled))
	SLOTS[name] = result
	return result


def slots(name: str, **shapes: object) -> Namespace:
	"""Declare a frozen namespace of typed slots."""
	return register(name, Namespace(name, {key: slot(f"{name}.{key}", value) for key, value in shapes.items()}, Runtime.SlotUnknown))


def Tuple(*shapes: object) -> Shape:
	"""Match fixed positions with an optional variable-length rest."""
	result = Shape("tuple", tuple(compileShape(item) for item in shapes))
	if sum(item.kind == "rest" for item in result.args) > 1:
		raise TypeError("Tuple allows at most one rest")
	return result


def rest(shape: object) -> Shape:
	"""Match zero or more tuple positions against `shape`."""
	return Shape("rest", (compileShape(shape),))


__ = rest(Any)


def loose(shape: object) -> Shape:
	"""Match declared struct fields while ignoring additional keys."""
	compiled = compileShape(shape)
	if compiled.kind != "struct":
		raise TypeError("loose requires a struct")
	return Shape("loose", compiled.args)


def either(*shapes: object) -> Shape:
	"""Choose the first successful matching alternative."""
	if not shapes:
		raise TypeError("either requires alternatives")
	return Shape("either", tuple(compileShape(item) for item in shapes))


def nullable(shape: object) -> Shape:
	"""Admit explicit null in addition to `shape`."""
	return either(shape, Null)


def where(predicate: object, name: str) -> Constraint:
	"""Register a pure predicate as the constraint `name`, a qualifier such as `"Even"`."""
	if not isinstance(name, str) or not NAME_RE.fullmatch(name):
		raise TypeError("constraints are named by a qualifier string, not a code")
	return register(name, Constraint(name, predicate))


def refine(shape: object, *constraints: Constraint) -> Shape:
	"""Apply constraints after structural matching, in order."""
	if not constraints or not all(isinstance(item, Constraint) for item in constraints):
		raise TypeError("refine requires named constraints")
	compiled = compileShape(shape)
	if compiled.kind == "refine":
		return Shape("refine", (compiled.args[0], compiled.args[1] + constraints))
	return Shape("refine", (compiled, constraints))


def via(slot: Slot, codec: Codec) -> Shape:
	"""Attach a source codec to one slot position."""
	if not isinstance(slot, Slot) or not isinstance(codec, Codec):
		raise TypeError("via requires a slot and an instantiated codec")
	return Shape("via", (slot, codec))


def optional(slot: Slot) -> Shape:
	"""Suppress expected absence of a template slot reference."""
	if not isinstance(slot, Slot):
		raise TypeError("optional requires a slot")
	return Shape("optional", (slot,))


def tolerant(shape: object) -> Shape:
	"""Keep matching when a position fails, marking its slots as failed."""
	return Shape("tolerant", (compileShape(shape),))


def gather(slot: Slot | Shape) -> Shape:
	"""Contribute an item to a collection slot within an enclosing list."""
	compiled = compileShape(slot)
	if not isCollectionSlot(compiled):
		raise TypeError("gather requires a collection slot")
	return Shape("gather", (compiled,))


def parsed(slot: Slot) -> Shape:
	"""Expand to the captured codec's name, when available."""
	if not isinstance(slot, Slot):
		raise TypeError("parsed requires a slot")
	return Shape("parsed", (slot,))


def pattern(source: str, *, registry: dict | None = None) -> Shape:
	"""Match a whole string with local or qualified captures and named codecs."""
	pieces, captures, offset = [], [], 0
	catalog = REGISTRY if registry is None else registry
	for found in re.finditer(r"\{([^{}]+)\}", source):
		literal = source[offset:found.start()]
		if "{" in literal or "}" in literal:
			raise ValueError("invalid pattern")
		pieces.append(re.escape(literal))
		name, separator, formatName = found.group(1).partition(":")
		if not NAME_RE.fullmatch(name):
			raise ValueError("invalid capture name")
		key = catalog.get(name) if "." in name or name in ("Location", "Origin") else name
		if not isinstance(key, (str, Slot)):
			raise Failure(Failure.Contract, Runtime.SlotUnknown)
		codecValue = catalog.get(formatName) if separator else None
		if separator and not isinstance(codecValue, Codec):
			raise Failure(Failure.Contract, Runtime.MemberUnknown)
		if isinstance(key, Slot) and not compatible(codecValue.canonical if codecValue else Text, key.shape):
			raise TypeError("pattern codec is incompatible with slot")
		captures.append((key, codecValue))
		pieces.append("(.*?)")
		offset = found.end()
	if "{" in source[offset:] or "}" in source[offset:]:
		raise ValueError("invalid pattern")
	pieces.append(re.escape(source[offset:]))
	return Shape("pattern", (source, re.compile("".join(pieces), re.DOTALL), tuple(captures)))


def lossy(value: object, reason: Member) -> Lossy:
	"""Return a canonical value with an explicitly declared loss reason."""
	if not isinstance(reason, Member):
		raise TypeError("loss reason must be a code")
	return Lossy(value, reason)


def errors(value: object, shape: object) -> list[dict]:
	"""Return all structural issues without including source values."""
	compiled = compileShape(shape)
	validateShape(compiled)
	issues = []
	matchShape(value, compiled, Captures(), issues)
	return issues


def check(value: object, shape: object, code: Member) -> object:
	"""Return `value` unchanged or raise a contract failure with `code`."""
	try:
		issues = errors(value, shape)
	except Gap as gap:
		raise Failure(Failure.Contract, code, gap.path) from gap
	if issues:
		failure = Failure(Failure.Contract, code, issues[0]["path"])
		failure.issues = issues
		raise failure
	return value


def capture(value: object, shape: object) -> Captures:
	"""Match and parse `value`, returning canonical captures and evidence."""
	compiled = compileShape(shape)
	validateShape(compiled)
	result, issues = Captures(), []
	matchShape(value, compiled, result, issues)
	if issues:
		failure = Failure(Failure.Shape, issues[0]["code"], issues[0]["path"])
		failure.issues = issues
		raise failure
	return result


def evidence(captures: dict, slot: Slot | str) -> dict | None:
	"""Return detached source evidence for `slot`, or None."""
	result = deepcopy(asCaptures(captures).evidence.get(slot))
	if result and "codec" in result and isinstance(result["codec"], list) and not any(result["codec"]):
		del result["codec"]
	return result

# EOF
