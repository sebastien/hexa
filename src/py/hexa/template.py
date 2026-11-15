"""Module: hexa.template — Compile templates, expand captures and declare derivations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from .core import ABSENT, Failure, Gap, Issue, codeNamespace, freeze, reasonNamed, register
from .shape import Shape, asCaptures, capture, checkOutput, children, compatible, compileShape, errors, isCollectionSlot, mergeCaptures, rest

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["transform", "derive", "expand", "reshape"]


@dataclass(frozen=True, slots=True, eq=False)
class Transform:
	"""A registered pure derivation with checked input and output shapes."""

	name: str
	function: object = field(repr=False)
	inputs: tuple
	output: Shape
	gaps: tuple = ()
	examples: tuple = ()

	def __getattr__(self, name):
		return reasonNamed(self.gaps, name)


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def parameterShapes(parameters, count):
	if parameters and parameters[-1].kind == "rest":
		if count < len(parameters) - 1:
			raise TypeError("too few derivation arguments")
		return parameters[:-1] + (parameters[-1].args[0],) * (count - len(parameters) + 1)
	if count != len(parameters):
		raise TypeError("derivation arguments do not match signature")
	return parameters


def compileTemplate(value):
	if isinstance(value, dict):
		return Shape("struct", tuple((key[:-1] if key.endswith("?") else key, key.endswith("?"), compileTemplate(item)) for key, item in value.items()))
	if isinstance(value, list):
		if len(value) == 1:
			child = compileShape(value[0])
			if isCollectionSlot(child):
				return Shape("list", (Shape("gather", (child,)),))
		return Shape("tuple", tuple(compileTemplate(item) for item in value))
	return compileShape(value)


def validateTemplateReferences(template, partial, quiet=False):
	"""Require slots captured by only some union branches to be optional in `template`."""
	if template.kind in ("slot", "via") and template.args[0] in partial and not quiet:
		raise TypeError("slots captured only by some union branches must be optional")
	if template.kind in ("struct", "loose"):
		for _, optionalKey, child in template.args:
			validateTemplateReferences(child, partial, quiet or optionalKey)
	elif template.kind == "derive":
		for child in template.args[1]:
			validateTemplateReferences(child, partial, quiet)
	else:
		for child in children(template):
			validateTemplateReferences(child, partial, quiet)


def expandTemplate(shape, captures, holes, path=(), quiet=False):
	kind, args = shape.kind, shape.args
	if kind in ("struct", "loose"):
		result = {}
		for key, isOptional, child in args:
			value = expandTemplate(child, captures, holes, (*path, key), quiet or isOptional)
			if value is not ABSENT:
				result[key] = value
		return result
	if kind in ("tuple", "list"):
		if kind == "list" and args[0].kind == "gather":
			item = args[0].args[0]
			key = item.args[0]
			if key not in captures:
				return expandTemplate(compileShape(key), captures, holes, path, quiet)
			if item.kind == "slot":
				return deepcopy(captures[key])
			result = {}
			codecValue = item.args[1]
			entry = captures.evidence.get(key, {})
			for index, value in enumerate(captures[key]):
				original = None
				if isinstance(entry.get("codec"), list) and entry["codec"][index] == codecValue.name:
					original = entry["raw"][index]
				try:
					result[index] = codecValue.convert(value, formatting=True, original=original)
				except Gap as failure:
					holes.append({"path": [*path, index], "reason": failure.code})
			return list(result.values()) if len(result) == len(captures[key]) else result
		values = {index: expandTemplate(child, captures, holes, (*path, index), quiet) for index, child in enumerate(args)}
		if any(value is ABSENT for value in values.values()):
			return {index: value for index, value in values.items() if value is not ABSENT}
		return list(values.values())
	if kind == "literal":
		return deepcopy(args[0])
	if kind in ("slot", "optional", "via", "parsed"):
		key = args[0]
		failed = captures.failed
		if key not in captures:
			if key in failed or (not quiet and kind not in ("optional", "parsed")):
				holes.append({"path": list(path), "reason": failed.get(key, Issue.Missing)})
			return ABSENT
		if kind == "parsed":
			codecName = captures.evidence.get(key, {}).get("codec", ABSENT)
			return deepcopy(codecName) if codecName is not ABSENT else ABSENT
		if kind == "via":
			codecValue = args[1]
			entry = captures.evidence.get(key, {})
			try:
				return deepcopy(codecValue.convert(captures[key], formatting=True, original=entry.get("raw") if entry.get("codec") == codecValue.name else None))
			except Gap as failure:
				holes.append({"path": list(path), "reason": failure.code})
				return ABSENT
		return deepcopy(captures[key])
	if kind == "derive":
		transformValue, arguments = args
		localHoles, values = [], []
		for argument in arguments:
			values.append(expandTemplate(argument, captures, localHoles, path))
		if localHoles:
			holes.append(localHoles[0])
			return ABSENT
		try:
			for value, expected in zip(values, parameterShapes(transformValue.inputs, len(values))):
				if value is not ABSENT and errors(value, expected):
					raise Failure(Failure.Defect, Issue.Mismatch)
			result = transformValue.function(*values)
		except Gap as failure:
			if failure.code not in transformValue.gaps:
				raise Failure(Failure.Defect, Issue.Unexpected) from failure
			holes.append({"path": list(path), "reason": failure.code})
			return ABSENT
		except Exception as error:
			raise Failure(Failure.Defect, Issue.Mismatch) from error
		checkOutput(result)
		if errors(result, transformValue.output):
			raise Failure(Failure.Defect, Issue.Mismatch)
		return deepcopy(result)
	raise TypeError("shape is not a template")


def outputShape(template):
	"""Derive the shape of values that expanding `template` produces."""
	if template.kind in ("slot", "optional"):
		return template.args[0].shape
	if template.kind == "via":
		return template.args[1].source
	if template.kind == "derive":
		return template.args[0].output
	if template.kind == "parsed":
		return compileShape(str)
	if template.kind in ("struct", "loose"):
		return Shape(template.kind, tuple((key, optionalKey or child.kind in ("optional", "parsed"), outputShape(child)) for key, optionalKey, child in template.args))
	if template.kind in ("tuple", "list"):
		if template.kind == "list" and template.args[0].kind == "gather":
			item = template.args[0].args[0]
			return item.args[0].shape if item.kind == "slot" else Shape("list", (item.args[1].source,))
		return Shape(template.kind, tuple(outputShape(child) for child in template.args))
	return template


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

def transform(name, function, *, inputs, output, gaps=(), examples=()):
	"""Register a pure derivation with checked input and output shapes."""
	if not callable(function):
		raise TypeError("transforms require a callable")
	parameters = []
	for index, item in enumerate(inputs):
		if isinstance(item, list) and len(item) == 2 and item[1] is Ellipsis:
			if index != len(inputs) - 1:
				raise TypeError("variadic parameter must be last")
			parameters.append(rest(item[0]))
		else:
			parameters.append(compileShape(item))
	if any(item.kind == "rest" and index != len(parameters) - 1 for index, item in enumerate(parameters)):
		raise TypeError("variadic parameter must be last")
	reasons = codeNamespace(name, gaps)
	result = Transform(name, function, tuple(parameters), compileShape(output), tuple(reasons.members.values()), tuple(freeze(item) for item in examples))
	return register(name, result)


def derive(transform: Transform, *arguments: object) -> Shape:
	"""Build a nested derivation without executing it."""
	if not isinstance(transform, Transform):
		raise TypeError("derivation arguments do not match signature")
	parameters = parameterShapes(transform.inputs, len(arguments))
	compiled = tuple(compileTemplate(item) for item in arguments)
	for item, expected in zip(compiled, parameters):
		if item.kind == "literal" and errors(item.args[0], expected):
			raise TypeError("invalid literal derivation argument")
		actual = item.args[0].shape if item.kind in ("slot", "optional") else item.args[0].output if item.kind == "derive" else None
		if actual is not None and not compatible(actual, expected):
			raise TypeError("derivation argument shape is incompatible with signature")
	return Shape("derive", (transform, compiled))


def expand(template: object, captures: dict) -> tuple[object, list[dict]]:
	"""Return a detached value and reason-bearing holes at template paths."""
	holes = []
	result = expandTemplate(compileTemplate(template), asCaptures(captures), holes)
	if result is ABSENT:
		raise Gap(holes[0]["reason"] if holes else Issue.Missing, [])
	return result, holes


def reshape(shape: object, template: object, value: object, captures: dict | None = None) -> object:
	"""Capture, join optional enclosing captures and expand a fresh value."""
	result = capture(value, shape)
	if captures is not None:
		mergeCaptures(result, asCaptures(captures))
	return expand(template, result)[0]

# EOF
