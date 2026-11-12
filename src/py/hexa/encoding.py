"""Module: hexa.encoding — Encode and decode configuration through the registry."""

from __future__ import annotations

from copy import deepcopy

from .codec import codec
from .core import LIMITS, REGISTRY, Constructor, Failure, Issue, Member, Namespace, Profile, Runtime, thaw, validateData
from . import profile as profiles
from .profile import CONSTRUCTOR_NAMES, ProfileDefinition, RequestFormat, admitData, permissionIssues
from .shape import Codec, CodecFamily, Constraint, Shape, Slot, Tuple, compileShape, either, gather, optional, parsed, pattern, refine, rest, tolerant, via
from .template import Transform, compileTemplate, derive

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["encode", "decode", "describe"]

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


def encodeProfile(value):
	definition = {"name": value.name}
	if value.constructors:
		definition["constructors"] = list(value.constructors)
	for key in ("types", "slots", "constraints", "derivations", "codecs"):
		items = getattr(value, key)
		if items:
			definition[key] = ["$" + (item.args[0] if key == "types" else item.name) for item in items]
	if value.shapes:
		definition["shapes"] = ["$" + name for name, _ in value.shapes]
	if value.requests:
		definition["requests"] = {item.name: thaw(options) for item, options in value.requests}
	if value.budgets:
		definition["budgets"] = dict(value.budgets)
	return {"$profile": definition}


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
	if isinstance(value, ProfileDefinition):
		return encodeProfile(value)
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


def decode(value: object, profile: ProfileDefinition | None = None, *, registry: dict | None = None, maxDepth: int = LIMITS["maxDepth"], maxNodes: int = LIMITS["maxNodes"], maxBytes: int = LIMITS["maxBytes"]) -> object:
	"""Resolve bounded JSON configuration through an explicit registry.

	Encoded configuration is data, so anything malformed is a `Failure.Contract`.
	Caller input is decoded under a `profile`: its budgets replace the size limits
	(`Profile.Exceeded`), and every term it does not permit is refused
	(`Profile.Forbidden`) before anything is constructed.
	"""
	catalog = REGISTRY if registry is None else registry
	if profile is not None:
		if not isinstance(profile, ProfileDefinition):
			raise TypeError("profile must be a declared profile")
		admitData(value, profile)
		issues = permissionIssues(value, profile, catalog)
		if issues:
			failure = Failure(Failure.Contract, Profile.Forbidden, issues[0]["path"])
			failure.issues = issues
			raise failure
	else:
		validateData(value, maxDepth=maxDepth, maxNodes=maxNodes, maxBytes=maxBytes)

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

	def visitProfile(item):
		definition = item["$profile"]
		name = definition["name"]
		if name in catalog:
			result = catalog[name]
			if not isinstance(result, ProfileDefinition) or encode(result) != item:
				raise Failure(Failure.Contract, Issue.Mismatch)
			return result
		if set(definition) - {"name", "constructors", "types", "shapes", "slots", "constraints", "derivations", "codecs", "requests", "budgets"}:
			raise Failure(Failure.Contract, Issue.Unexpected)
		references = {key: [visit(child) for child in definition.get(key, [])] for key in ("types", "slots", "constraints", "derivations", "codecs")}
		shapes = {}
		for child in definition.get("shapes", []):
			if not isinstance(child, str) or not child.startswith("$"):
				raise Failure(Failure.Contract, Issue.Mismatch)
			shapes[child[1:]] = resolve(child[1:])
		requests = {}
		for key, options in definition.get("requests", {}).items():
			format = resolve(key)
			if not isinstance(format, RequestFormat):
				raise Failure(Failure.Contract, Issue.Mismatch)
			requests[format] = options
		constructors = [CONSTRUCTOR_NAMES[child] for child in definition.get("constructors", [])]
		# NOTE: `decode`'s `profile` parameter shadows the declaration function here.
		return profiles.profile(name, constructors=constructors, shapes=shapes, requests=requests, budgets=definition.get("budgets"), **references)

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
		if operator == "$profile":
			return visitProfile(item)
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



def describe(profile: ProfileDefinition) -> dict:
	"""Describe exactly the vocabulary `profile` permits, as data for `CatalogDescribe`."""
	if not isinstance(profile, ProfileDefinition):
		raise TypeError("profile must be a declared profile")
	slots = {}
	for item in profile.slots:
		for child in (item.members.values() if isinstance(item, Namespace) else (item,)):
			slots[child.name] = encode(child.shape)
	derivations = {}
	for item in profile.derivations:
		description = {"inputs": [encode(shape) for shape in item.inputs], "output": encode(item.output), "gaps": [gap.short for gap in item.gaps]}
		if item.examples:
			description["examples"] = thaw(item.examples)
		derivations[item.name] = description
	return {
		"constructors": list(profile.constructors),
		"types": ["$" + item.args[0] for item in profile.types],
		"shapes": {name: encode(shape) for name, shape in profile.shapes},
		"slots": slots,
		"constraints": ["$" + item.name for item in profile.constraints],
		"derivations": derivations,
		"codecs": {item.name: encodeCodec(item) for item in profile.codecs},
		"requests": {item.name: {"gaps": [gap.short for gap in item.gaps], **thaw(options)} for item, options in profile.requests},
	}

# EOF
