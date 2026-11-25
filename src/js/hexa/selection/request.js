/** Module: hexa.selection.request — Selection request validation and planning. */

import { Failure, Issue, Profile, isPlain } from "../core.js";
import { request } from "../profile.js";
import { Int, Struct, Text, refine, where } from "../shape.js";

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

function contractFailure(issues) {
	const failure = new Failure(Failure.Contract, issues[0].code, { path: issues[0].path });
	failure.issues = issues;
	throw failure;
}

// -----------------------------------------------------------------------------
//
// OPERATIONS
//
// -----------------------------------------------------------------------------

/** Validate caller selections, collecting value-free contract issues. */
function selectionValidate(options, value, catalog) {
	const issues = [], entitySets = catalog.entitySets ?? {}, relationTypes = catalog.relations ?? {};
	const add = (code, ...path) => issues.push({ code, path });
	function keys(item, allowed, required, ...path) {
		if (!isPlain(item)) { add(Issue.Mismatch, ...path); return false; }
		for (const key of Object.keys(item)) if (!allowed.includes(key)) add(Issue.Unexpected, ...path, key);
		for (const key of required) if (!Object.hasOwn(item, key)) add(Issue.Missing, ...path, key);
		return true;
	}
	if (!keys(value, ["version", "selections", "relations"], ["version", "selections"])) contractFailure(issues);
	const result = { version: value.version, selections: {}, relations: [] };
	if (Object.hasOwn(value, "version") && (!Number.isInteger(value.version) || !options.versions.includes(value.version))) add(Profile.Forbidden, "version");
	if (isPlain(value.selections)) {
		if (Object.keys(value.selections).length < 1 || Object.keys(value.selections).length > options.maxSelections) add(Issue.Constraint, "selections");
		for (const [alias, selection] of Object.entries(value.selections)) {
			if (!/^[A-Z][A-Za-z0-9]{0,63}$/.test(alias)) add(Issue.Mismatch, "selections", alias);
			if (!keys(selection, ["from", "attributes"], ["from", "attributes"], "selections", alias)) continue;
			const source = selection.from, attributes = selection.attributes;
			const permitted = typeof source === "string" && Object.hasOwn(entitySets, source) && Object.hasOwn(options.entitySets, source) ? options.entitySets[source] : null;
			if (!permitted) add(Profile.Forbidden, "selections", alias, "from");
			if (!Array.isArray(attributes) || attributes.some(item => typeof item !== "string")) { add(Issue.Mismatch, "selections", alias, "attributes"); continue; }
			if (new Set(attributes).size !== attributes.length) add(Issue.Constraint, "selections", alias, "attributes");
			attributes.forEach((attribute, index) => {
				if (permitted && (!permitted.includes(attribute) || !Object.hasOwn(entitySets[source].attributes ?? {}, attribute))) add(Profile.Forbidden, "selections", alias, "attributes", index);
			});
			Object.defineProperty(result.selections, alias, { value: { from: source, attributes: [...attributes] }, enumerable: true });
		}
	} else if (Object.hasOwn(value, "selections")) add(Issue.Mismatch, "selections");
	let relations = Object.hasOwn(value, "relations") ? value.relations : [];
	if (!Array.isArray(relations)) { add(Issue.Mismatch, "relations"); relations = []; }
	if (relations.length > options.maxRelations) add(Issue.Constraint, "relations");
	const seen = new Set();
	relations.forEach((relation, index) => {
		if (!keys(relation, ["type", "participants"], ["type", "participants"], "relations", index)) return;
		const kind = relation.type, participants = relation.participants;
		if (typeof kind !== "string" || !options.relations.includes(kind) || !Object.hasOwn(relationTypes, kind)) { add(Profile.Forbidden, "relations", index, "type"); return; }
		if (seen.has(kind)) add(Issue.Constraint, "relations", index, "type");
		seen.add(kind);
		const roles = relationTypes[kind].roles ?? {}, normalised = {};
		if (!keys(participants, Object.keys(roles), Object.keys(roles), "relations", index, "participants")) return;
		for (const [role, members] of Object.entries(participants)) {
			if (!Object.hasOwn(roles, role)) continue;
			const aliases = typeof members === "string" ? [members] : members;
			if (!Array.isArray(aliases) || !aliases.length || aliases.some(item => typeof item !== "string")) { add(Issue.Mismatch, "relations", index, "participants", role); continue; }
			for (const alias of aliases) {
				if (!Object.hasOwn(result.selections, alias)) add(Issue.Missing, "relations", index, "participants", role);
				else if (entitySets[result.selections[alias].from]?.type !== roles[role]) add(Issue.Mismatch, "relations", index, "participants", role);
			}
			normalised[role] = [...aliases];
		}
		result.relations.push({ type: kind, participants: normalised });
	});
	if (issues.length) contractFailure(issues);
	return result;
}

/** Compile validated selections into catalog steps and plan gaps. */
function selectionCompile(options, value, catalog) {
	const steps = catalog.steps ?? {}, entitySets = catalog.entitySets, relationTypes = catalog.relations ?? {};
	const needed = new Set(), gaps = [];
	function add(identifier) {
		if (!Object.hasOwn(steps, identifier)) throw new Failure(Failure.Defect, Issue.Missing);
		if (!needed.has(identifier)) { needed.add(identifier); for (const required of steps[identifier].requires ?? []) add(required); }
	}
	function supply(entry, path) {
		const correspondences = entry.correspondences ?? [], resolved = correspondences.filter(item => item.resolved);
		const suppliers = resolved.filter(item => Object.hasOwn(steps, item.supplier)).map(item => item.supplier);
		const code = !correspondences.length ? SelectionRequest.Unmapped : !resolved.length ? SelectionRequest.Unresolved : !suppliers.length ? SelectionRequest.NotSupplied : null;
		if (code) { gaps.push({ code, path }); return false; }
		suppliers.forEach(add);
		return true;
	}
	const selected = new Set(Object.values(value.selections).map(selection => entitySets[selection.from].type));
	for (const [alias, selection] of Object.entries(value.selections)) {
		const entitySet = entitySets[selection.from];
		for (const id of entitySet.requires ?? []) add(id);
		selection.attributes.forEach((attribute, index) => {
			const entry = entitySet.attributes[attribute], path = ["selections", alias, "attributes", index];
			for (const [i, link] of (entry.via ?? []).entries()) {
				if (!Object.hasOwn(relationTypes, link)) throw new Failure(Failure.Defect, Issue.Missing);
				if (!supply(relationTypes[link], [...path, "via", i])) return;
			}
			if (supply(entry, path) && entry.reference != null && !selected.has(entry.reference)) gaps.push({ code: SelectionRequest.ReferenceUnselected, path });
		});
	}
	value.relations.forEach((relation, index) => supply(relationTypes[relation.type], ["relations", index]));
	// The catalog defines plan order, even when dependencies are discovered later.
	const ordered = Object.keys(steps).filter(key => needed.has(key));
	return { steps: ordered.map(step => ({ step })), allow: [...new Set(ordered.filter(key => steps[key].capability).map(key => steps[key].capability))], gaps };
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

const SelectionEntitySets = where(value => isPlain(value) && Object.values(value).every(items => Array.isArray(items) && items.every(item => typeof item === "string")), "SelectionEntitySets");
export const SelectionRequest = request("SelectionRequest", {
	versions: [1], options: { maxSelections: Int, maxRelations: Int, entitySets: refine(Struct, SelectionEntitySets), relations: [Text] },
	validate: selectionValidate, compile: selectionCompile, gaps: ["Unmapped", "Unresolved", "NotSupplied", "ReferenceUnselected"],
});

// EOF
