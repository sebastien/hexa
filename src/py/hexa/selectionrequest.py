"""Module: hexa.selectionrequest — Selection request validation and planning."""

from __future__ import annotations

import re

from .core import Failure, Issue, Profile
from .profile import request
from .shape import Int, Struct, Text, where

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["SelectionRequest"]

ALIAS_RE = re.compile(r"[A-Z][A-Za-z0-9]{0,63}")


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def contractFailure(issues):
	"""Raise a contract failure containing all validation `issues`."""
	failure = Failure(Failure.Contract, issues[0]["code"], issues[0]["path"])
	failure.issues = issues
	raise failure


def selectionOptionsValid(value):
	"""Check that `value` maps entity-set names to lists of attribute names."""
	return isinstance(value, dict) and all(isinstance(items, list) and all(type(item) is str for item in items) for items in value.values())


# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------

def selectionValidate(options, value, catalog):
	"""Validate a selection request, accumulating every caller contract issue."""
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

	if not keys(value, {"version", "selections", "relations"}, {"version", "selections"}):
		contractFailure(issues)
	selections = value.get("selections")
	result = {"version": value.get("version"), "selections": {}, "relations": []}
	if "version" in value and (type(value["version"]) is not int or value["version"] not in options["versions"]):
		add(Profile.Forbidden, "version")
	if isinstance(selections, dict):
		if not 1 <= len(selections) <= options["maxSelections"]:
			add(Issue.Constraint, "selections")
		for alias, selection in selections.items():
			if not isinstance(alias, str) or not ALIAS_RE.fullmatch(alias):
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
				else:
					source = result["selections"][alias]["from"]
					if not isinstance(source, str) or entitySets.get(source, {}).get("type") != roles[role]:
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
			# Trusted catalog paths are ordered proof obligations, not steps supplied by the caller.
			for i, link in enumerate(entry.get("via", [])):
				if link not in relationTypes:
					raise Failure(Failure.Defect, Issue.Missing)
				if not supply(relationTypes[link], [*path, "via", i]):
					break
			else:
				if not supply(entry, path) or entry.get("reference") is None or entry["reference"] in selected:
					continue
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

SelectionEntitySets = where(selectionOptionsValid, "SelectionEntitySets")
SelectionRequest = request("SelectionRequest", versions=[1],
	options={"maxSelections": Int, "maxRelations": Int, "entitySets": Struct & SelectionEntitySets, "relations": [Text]},
	validate=selectionValidate, compile=selectionCompile,
	gaps=["Unmapped", "Unresolved", "NotSupplied", "ReferenceUnselected"])

# EOF
