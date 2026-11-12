"""Module: hexa.profile — Profiles, request formats and the selection request format.

A profile is the host's allow-list for caller input (spec-004 Profiles). A request
format validates caller requests and compiles them into plans (spec-004 Requests and
plans). Decoding under a profile lives in `encoding`; the stages that apply formats
are the combinators `validate` and `plan`.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

from .core import LIMITS, REGISTRY, Failure, Issue, Namespace, Profile, freeze, register, thaw, validateData
from .shape import (
	NAME_RE, Codec, CodecFamily, Constraint, Int, Shape, Slot, Struct, Text, Tuple, check, codeNamespace,
	compileShape, either, gather, loose, parsed, pattern, reasonNamed, refine, rest, tolerant, where,
)
from .template import Transform, derive

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["ProfileDefinition", "RequestFormat", "profile", "request", "admitData", "permissionIssues", "SelectionRequest", "Profile"]

# NOTE: Encoded constructor names (`$loose`, ...) by the function that builds them.
CONSTRUCTORS = {loose: "loose", Tuple: "tuple", rest: "rest", either: "either", refine: "refine", pattern: "pattern",
	tolerant: "tolerant", gather: "gather", parsed: "parsed", derive: "derive"}
CONSTRUCTOR_NAMES = {name: function for function, name in CONSTRUCTORS.items()}
BUDGET_KEYS = ("maxDepth", "maxNodes", "maxBytes")
PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
ALIAS_RE = re.compile(r"[A-Z][A-Za-z0-9]{0,63}")


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


def contractFailure(issues):
	"""Raise one `Failure.Contract` carrying every collected issue."""
	failure = Failure(Failure.Contract, issues[0]["code"], issues[0]["path"])
	failure.issues = issues
	raise failure


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


def selectionOptionsValid(value):
	return isinstance(value, dict) and all(isinstance(items, list) and all(type(item) is str for item in items) for items in value.values())


def selectionValidate(options, value, catalog):
	"""Validate a selection request against profile options and the host catalog."""
	issues = []
	entitySets, relationTypes = catalog.get("entitySets", {}), catalog.get("relations", {})

	def add(code, *path):
		issues.append({"code": code, "path": list(path)})

	def keys(item, allowed, required, *path):
		if not isinstance(item, dict):
			add(Issue.Mismatch, *path)
			return False
		for key in item.keys() - allowed:
			add(Issue.Unexpected, *path, key)
		for key in required - item.keys():
			add(Issue.Missing, *path, key)
		return True

	keys(value, {"version", "selections", "relations"}, {"version", "selections"})
	selections = value.get("selections")
	result = {"version": value["version"], "selections": {}, "relations": []}
	if isinstance(selections, dict):
		if not 1 <= len(selections) <= options["maxSelections"]:
			add(Issue.Constraint, "selections")
		for alias, selection in selections.items():
			if not ALIAS_RE.fullmatch(alias):
				add(Issue.Mismatch, "selections", alias)
			if not keys(selection, {"from", "attributes"}, {"from", "attributes"}, "selections", alias):
				continue
			source, attributes = selection.get("from"), selection.get("attributes")
			permitted = options["entitySets"].get(source) if isinstance(source, str) and source in entitySets else None
			if permitted is None:
				add(Profile.Forbidden, "selections", alias, "from")
			if not isinstance(attributes, list) or any(type(item) is not str for item in attributes):
				add(Issue.Mismatch, "selections", alias, "attributes")
				continue
			if len(set(attributes)) != len(attributes):
				add(Issue.Constraint, "selections", alias, "attributes")
			for index, attribute in enumerate(attributes):
				if permitted is not None and (attribute not in permitted or attribute not in entitySets[source].get("attributes", {})):
					add(Profile.Forbidden, "selections", alias, "attributes", index)
			result["selections"][alias] = {"from": source, "attributes": list(attributes)}
	elif "selections" in value:
		add(Issue.Mismatch, "selections")
	relations = value.get("relations", [])
	if not isinstance(relations, list):
		add(Issue.Mismatch, "relations")
		relations = []
	if len(relations) > options["maxRelations"]:
		add(Issue.Constraint, "relations")
	seen = set()
	for index, relation in enumerate(relations):
		if not keys(relation, {"type", "participants"}, {"type", "participants"}, "relations", index):
			continue
		kind, participants = relation.get("type"), relation.get("participants")
		if not isinstance(kind, str) or kind not in options["relations"] or kind not in relationTypes:
			add(Profile.Forbidden, "relations", index, "type")
			continue
		if kind in seen:
			add(Issue.Constraint, "relations", index, "type")
		seen.add(kind)
		roles = relationTypes[kind].get("roles", {})
		if not keys(participants, set(roles), set(roles), "relations", index, "participants"):
			continue
		normalised = {}
		for role, aliases in participants.items():
			if role not in roles:
				continue
			aliases = [aliases] if isinstance(aliases, str) else aliases
			if not isinstance(aliases, list) or not aliases or any(type(item) is not str for item in aliases):
				add(Issue.Mismatch, "relations", index, "participants", role)
				continue
			for alias in aliases:
				if alias not in result["selections"]:
					add(Issue.Missing, "relations", index, "participants", role)
				elif entitySets[result["selections"][alias]["from"]].get("type") != roles[role]:
					add(Issue.Mismatch, "relations", index, "participants", role)
			normalised[role] = list(aliases)
		result["relations"].append({"type": kind, "participants": normalised})
	if issues:
		contractFailure(issues)
	return result


def selectionCompile(options, value, catalog):
	"""Compile a validated selection request into catalog steps and plan gaps."""
	steps, entitySets, relationTypes = catalog.get("steps", {}), catalog["entitySets"], catalog.get("relations", {})
	needed, gaps = set(), []

	def add(identifier):
		if identifier not in steps:
			raise Failure(Failure.Defect, Issue.Missing)
		if identifier not in needed:
			needed.add(identifier)
			for required in steps[identifier].get("requires", []):
				add(required)

	def supply(entry, path):
		correspondences = entry.get("correspondences", [])
		resolved = [item for item in correspondences if item.get("resolved")]
		suppliers = [item["supplier"] for item in resolved if item.get("supplier") in steps]
		code = (SelectionRequest.Unmapped if not correspondences else SelectionRequest.Unresolved if not resolved
			else SelectionRequest.NotSupplied if not suppliers else None)
		if code is not None:
			gaps.append({"code": code, "path": path})
			return False
		for identifier in suppliers:
			add(identifier)
		return True

	selected = {entitySets[selection["from"]].get("type") for selection in value["selections"].values()}
	for alias, selection in value["selections"].items():
		entitySet = entitySets[selection["from"]]
		for identifier in entitySet.get("requires", []):
			add(identifier)
		for index, attribute in enumerate(selection["attributes"]):
			entry, path = entitySet["attributes"][attribute], ["selections", alias, "attributes", index]
			if supply(entry, path) and entry.get("reference") is not None and entry["reference"] not in selected:
				gaps.append({"code": SelectionRequest.ReferenceUnselected, "path": path})
	for index, relation in enumerate(value["relations"]):
		supply(relationTypes[relation["type"]], ["relations", index])
	ordered = [identifier for identifier in steps if identifier in needed]
	allow = list(dict.fromkeys(steps[identifier]["capability"] for identifier in ordered if "capability" in steps[identifier]))
	return {"steps": [{"step": identifier} for identifier in ordered], "allow": allow, "gaps": gaps}


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


SelectionEntitySets = where(selectionOptionsValid, "SelectionEntitySets")

SelectionRequest = request("SelectionRequest", versions=[1],
	options={"maxSelections": Int, "maxRelations": Int, "entitySets": Struct & SelectionEntitySets, "relations": [Text]},
	validate=selectionValidate, compile=selectionCompile,
	gaps=["Unmapped", "Unresolved", "NotSupplied", "ReferenceUnselected"])

# EOF
