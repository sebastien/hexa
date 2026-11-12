"""Module: hexa.encoding — Encode and decode configuration through the registry."""

from __future__ import annotations

from copy import deepcopy

from .codec import codec
from .core import LIMITS, REGISTRY, Constructor, Failure, Issue, Member, Namespace, Runtime, thaw, validateData
from .shape import Codec, CodecFamily, Constraint, Shape, Slot, Tuple, compileShape, either, gather, optional, parsed, pattern, refine, rest, tolerant, via
from .template import Transform, compileTemplate, derive

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["encode", "decode"]

# NOTE: `$` keys accepted next to each operator in an encoded object.
OPERATOR_OPTIONS = {"$": {"via", "optional", "arguments", "value", "stage"}, "$refine": {"constraints"}, "$derive": {"arguments"}}
UNARY_OPERATORS = {"$pattern": pattern, "$tolerant": tolerant, "$gather": gather, "$parsed": parsed, "$rest": rest}

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def escapeKey(key):
	return "$" + key if key.startswith("$") else key


def encodeCodecReference(value):
	"""Reference a declared codec by name, or a family instance by family and arguments."""
	return {"$": value.family.name, "arguments": thaw(value.arguments)} if value.family else value.name


def encodeCodec(value):
	if isinstance(value, Codec) and value.family:
		return encodeCodecReference(value)
	definition = {"name": value.name, "source": encode(value.source), "canonical": encode(value.canonical)}
	if isinstance(value, Codec) and value.table is not None:
		definition.update(map={key: list(item) if isinstance(item, tuple) else item for key, item in value.table.items()}, gaps=dict(value.gapTable or {}))
	else:
		definition.update(gaps=[item.short for item in value.gaps], losses=[item.short for item in value.losses])
	if isinstance(value, CodecFamily):
		definition["params"] = {key: encode(item) for key, item in value.params}
	if value.examples:
		definition["examples"] = thaw(value.examples)
	return {"$codec": definition}


def encodeShape(value):
	kind, args = value.kind, value.args
	if kind == "type":
		return "$" + args[0]
	if kind == "literal":
		return encode(args[0])
	if kind == "slot":
		return encode(args[0])
	if kind == "parsed":
		return {"$parsed": encode(args[0])}
	if kind == "optional":
		return {"$": args[0].name, "optional": True}
	if kind == "via":
		return {"$": args[0].name, "via": encodeCodecReference(args[1])}
	if kind in ("struct", "loose"):
		result = {escapeKey(key) + ("?" if isOptional else ""): encode(child) for key, isOptional, child in args}
		return {"$loose": result} if kind == "loose" else result
	if kind == "list":
		return [encode(args[0].args[0] if args[0].kind == "gather" else args[0])]
	if kind in ("tuple", "either"):
		return {"$" + kind: [encode(item) for item in args]}
	if kind == "refine":
		return {"$refine": encode(args[0]), "constraints": [encode(item) for item in args[1]]}
	if kind == "pattern":
		return {"$pattern": args[0]}
	if kind == "derive":
		return {"$derive": encode(args[0]), "arguments": [encode(item) for item in args[1]]}
	return {"$" + kind: encode(args[0])}


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

def encode(value: object) -> object:
	"""Encode registered configuration without serializing executable code."""
	if isinstance(value, (Slot, Member, Constraint, Transform)):
		return "$" + value.name
	if isinstance(value, Namespace):
		return {key: encode(item) for key, item in value.members.items()}
	if isinstance(value, (Codec, CodecFamily)):
		return encodeCodec(value)
	if isinstance(value, Shape):
		return encodeShape(value)
	# NOTE: Higher layers (stages, policies, effects) encode themselves.
	if callable(getattr(type(value), "encoded", None)):
		return value.encoded()
	if isinstance(value, str):
		return escapeKey(value)
	if isinstance(value, dict):
		if set(value) == {"operation", "arguments"}:
			validateData(value)
			return deepcopy(value)
		return {escapeKey(key): encode(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [encode(item) for item in value]
	if value is None or type(value) in (int, float, bool):
		validateData(value)
		return value
	raise TypeError("configuration cannot be encoded")


def decode(value: object, registry: dict | None = None, *, maxDepth: int = LIMITS["maxDepth"], maxNodes: int = LIMITS["maxNodes"], maxBytes: int = LIMITS["maxBytes"]) -> object:
	"""Resolve bounded JSON configuration through an explicit registry.

	Encoded configuration is data, so anything malformed is a `Failure.Contract`.
	"""
	validateData(value, maxDepth=maxDepth, maxNodes=maxNodes, maxBytes=maxBytes)
	catalog = REGISTRY if registry is None else registry

	def resolve(name):
		if name not in catalog:
			raise Failure(Failure.Contract, Runtime.MemberUnknown)
		return catalog[name]

	def visitStruct(item):
		fields = []
		for key, child in item.items():
			verbatim = key.startswith("$$")
			optionalKey = key.endswith("?") and not verbatim
			name = key[1:] if verbatim else key[:-1] if optionalKey else key
			decoded = visit(child)
			fields.append((name, optionalKey, compileTemplate(decoded) if isinstance(decoded, list) and len(decoded) != 1 else compileShape(decoded)))
		return Shape("struct", tuple(fields))

	def visitReference(item):
		result = resolve(item["$"])
		if isinstance(result, Constructor) and ("value" in item or "stage" in item):
			return result(item["value"]) if "value" in item else result(resolve(item["stage"]))
		if "arguments" in item:
			if not isinstance(result, CodecFamily):
				raise Failure(Failure.Contract, Issue.Mismatch)
			result = result(*item["arguments"])
		if "via" in item:
			result = via(result, visit(item["via"]) if isinstance(item["via"], dict) else resolve(item["via"]))
		if item.get("optional"):
			result = optional(result)
		return result

	def visitCodec(item):
		definition = item["$codec"]
		name = definition["name"]
		if name in catalog:
			result = catalog[name]
			if not isinstance(result, (Codec, CodecFamily)) or encode(result) != item:
				raise Failure(Failure.Contract, Issue.Mismatch)
			return result
		if "map" not in definition:
			raise Failure(Failure.Contract, Runtime.MemberUnknown)
		return codec(name, source=visit(definition["source"]), canonical=visit(definition["canonical"]), map=definition["map"], gaps=definition.get("gaps", {}))

	def visit(item):
		if isinstance(item, str):
			if item.startswith("$$"):
				return Shape("literal", (item[1:],))
			return resolve(item[1:]) if item.startswith("$") else item
		if isinstance(item, list):
			return [visit(child) for child in item]
		if not isinstance(item, dict):
			return item
		if set(item) == {"operation", "arguments"}:
			return deepcopy(item)
		operators = [key for key in item if key.startswith("$") and not key.startswith("$$")]
		if not operators:
			if any(key.startswith("$$") for key in item):
				return visitStruct(item)
			return {key: visit(child) for key, child in item.items()}
		if len(operators) != 1:
			raise Failure(Failure.Contract, Issue.Unexpected)
		operator = operators[0]
		if set(item) - {operator} - OPERATOR_OPTIONS.get(operator, set()):
			raise Failure(Failure.Contract, Issue.Unexpected)
		argument = item[operator]
		if operator == "$":
			return visitReference(item)
		if operator == "$codec":
			return visitCodec(item)
		if operator in ("$tuple", "$either"):
			return (Tuple if operator == "$tuple" else either)(*(visit(child) for child in argument))
		if operator == "$loose":
			result = compileShape(visit(argument))
			if result.kind != "struct":
				raise Failure(Failure.Contract, Issue.Mismatch)
			return Shape("loose", result.args)
		if operator == "$refine":
			return refine(visit(argument), *(visit(child) for child in item["constraints"]))
		if operator == "$derive":
			return derive(visit(argument), *(visit(child) for child in item["arguments"]))
		if operator not in UNARY_OPERATORS:
			raise Failure(Failure.Contract, Issue.Unexpected)
		if operator == "$pattern":
			return pattern(argument, registry=catalog)
		return UNARY_OPERATORS[operator](visit(argument))

	try:
		return visit(value)
	except (TypeError, ValueError, KeyError) as error:
		# Constructors reject malformed arguments as programming errors; here they came from data.
		raise Failure(Failure.Contract, Issue.Mismatch) from error

# EOF
