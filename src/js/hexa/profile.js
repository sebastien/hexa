/** Module: hexa.profile — Caller permissions and generic request formats. */

import { LIMITS, NAMESPACES, REGISTRY, Failure, Issue, NAME_RE, Profile, clone, freeze, isPlain, namespace, register, validateData, withReasons } from "./core.js";
import { Codec, Constraint, Shape, Slot, Tuple, check, compileShape, either, gather, loose, parsed, pattern, refine, rest, tolerant } from "./shape.js";
import { Transform, derive } from "./template.js";

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

/** Map constructor functions to their interchange names. */
export const CONSTRUCTORS = new Map([[loose, "loose"], [Tuple, "tuple"], [rest, "rest"], [either, "either"], [refine, "refine"], [pattern, "pattern"], [tolerant, "tolerant"], [gather, "gather"], [parsed, "parsed"], [derive, "derive"]]);
/** Map interchange names back to constructor functions. */
export const CONSTRUCTOR_NAMES = new Map([...CONSTRUCTORS].map(([fn, name]) => [name, fn]));

/** Defines the vocabulary and budgets allowed to a caller. */
export class ProfileDefinition {
	constructor(definition) {
		Object.assign(this, definition, { profileDefinition: true });
		Object.freeze(this);
	}

	/** Return default limits overridden by this profile's budgets. */
	get limits() { return { ...LIMITS, ...this.budgets }; }
	/** Return detached options for `format`, or null when it is not permitted. */
	options(format) { return clone(this.requests.find(([item]) => item === format)?.[1] ?? null); }

	/** Check whether `value` is permitted by this profile. */
	permits(value) {
		if (value instanceof Slot) return this.slots.some(item => item === value || Object.values(NAMESPACES.get(item)?.members ?? {}).includes(value));
		if (value instanceof Shape) return value.kind === "type" ? this.types.includes(value) : this.shapes.some(([, shape]) => shape === value);
		if (value instanceof Constraint) return this.constraints.includes(value);
		if (value instanceof Transform) return this.derivations.includes(value);
		if (value instanceof Codec || typeof value === "function" && value.codecFamily) return this.codecs.some(item => item === value || value.family === item);
		return false;
	}
}

/** Defines validation and compilation for a request format. */
export class RequestFormat {
	constructor(definition) { Object.assign(this, definition); }
	/** Reject unsupported versions or invalid format `options`. */
	checkOptions(options) {
		if (!isPlain(options) || !Array.isArray(options.versions) || !options.versions.length || options.versions.some(item => !Number.isInteger(item) || !this.versions.includes(item))) throw new TypeError("invalid request versions");
		check(Object.fromEntries(Object.entries(options).filter(([key]) => key !== "versions")), this.options, Issue.Mismatch);
	}
}

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

function registered(value, test) {
	if (!test(value) || REGISTRY.get(value?.name) !== value) throw new TypeError("profile terms must be registered");
}

/** Check all permissions before any caller-supplied term is constructed.
 * Keep this syntax traversal aligned with the decoder in `encoding.js`.
 */
export function permissionIssues(value, definition, catalog = REGISTRY) {
	const issues = [];
	const refuse = path => issues.push({ code: Profile.Forbidden, path });
	const name = (item, path) => { if (typeof item !== "string" || !definition.permits(catalog.get(item))) refuse(path); };
	function visit(item, path) {
		if (typeof item === "string") { if (item.startsWith("$") && !item.startsWith("$$")) name(item.slice(1), path); return; }
		if (Array.isArray(item)) { item.forEach((child, index) => visit(child, [...path, index])); return; }
		if (!isPlain(item)) return;
		const operators = Object.keys(item).filter(key => key.startsWith("$") && !key.startsWith("$$"));
		if (!operators.length) {
			if (Object.keys(item).length === 2 && Object.hasOwn(item, "operation") && Object.hasOwn(item, "arguments")) { refuse(path); return; }
			for (const [key, child] of Object.entries(item)) visit(child, [...path, key]);
			return;
		}
		if (operators.length !== 1) return;
		const operator = operators[0];
		if (operator === "$") {
			if (Object.hasOwn(item, "value") || Object.hasOwn(item, "stage")) { refuse(path); return; }
			name(item.$, path);
			if (Object.hasOwn(item, "via")) name(isPlain(item.via) ? item.via.$ : item.via, [...path, "via"]);
			return;
		}
		if (!definition.constructors.includes(operator.slice(1))) { refuse([...path, operator]); return; }
		if (operator === "$pattern") {
			if (typeof item[operator] === "string") for (const found of item[operator].matchAll(/\{([^{}]+)\}/g)) {
				const [slotName, codecName] = found[1].split(":");
				if (slotName.includes(".") || ["Location", "Origin"].includes(slotName)) name(slotName, [...path, operator]);
				if (codecName) name(codecName, [...path, operator]);
			}
			return;
		}
		visit(item[operator], [...path, operator]);
		for (const key of ["constraints", "arguments"]) if (Object.hasOwn(item, key)) visit(item[key], [...path, key]);
	}
	visit(value, []);
	return issues;
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/** Register a request format and its gap codes. */
export function request(name, { versions, options, validate, compile, gaps = [] }) {
	if (!NAME_RE.test(name) || !versions.length || versions.some(item => !Number.isInteger(item)) || typeof validate !== "function" || typeof compile !== "function") throw new TypeError("invalid request format");
	const reasons = namespace(name, gaps);
	return register(name, withReasons(new RequestFormat({ name, versions: freeze([...versions]), options: compileShape(options), validate, compile, gaps: Object.values(reasons) }), reasons));
}

/** Register the exact vocabulary and budgets permitted to a caller. */
export function profile(name, { constructors = [], types = [], shapes = {}, slots = [], constraints = [], derivations = [], codecs = [], requests = new Map(), budgets = {} } = {}) {
	if (!NAME_RE.test(name) || constructors.some(item => !CONSTRUCTORS.has(item))) throw new TypeError("invalid profile");
	if (types.some(item => !(item instanceof Shape) || item.kind !== "type")) throw new TypeError("invalid types");
	const named = Object.entries(shapes).map(([key, value]) => {
		if (!NAME_RE.test(key) || key.includes(".")) throw new TypeError("invalid shape name");
		return [key, register(key, compileShape(value))];
	});
	for (const item of slots) {
		const ns = NAMESPACES.get(item);
		if (ns) { if (REGISTRY.get(ns.name) !== item || Object.values(ns.members).some(child => !(child instanceof Slot))) throw new TypeError("invalid slot namespace"); }
		else registered(item, value => value instanceof Slot);
	}
	for (const item of constraints) registered(item, value => value instanceof Constraint);
	for (const item of derivations) registered(item, value => value instanceof Transform);
	for (const item of codecs) registered(item, value => value instanceof Codec || typeof value === "function" && value.codecFamily);
	const formats = [...requests].map(([format, options]) => {
		registered(format, value => value instanceof RequestFormat);
		format.checkOptions(options);
		return [format, freeze(clone(options))];
	});
	for (const [key, limit] of Object.entries(budgets)) if (!Object.hasOwn(LIMITS, key) || !Number.isSafeInteger(limit) || limit < 1 || limit > LIMITS[key]) throw new TypeError("invalid profile budget");
	return register(name, new ProfileDefinition({ name, constructors: freeze(constructors.map(item => CONSTRUCTORS.get(item))), types: freeze([...types]), shapes: freeze(named), slots: freeze([...slots]), constraints: freeze([...constraints]), derivations: freeze([...derivations]), codecs: freeze([...codecs]), requests: freeze(formats), budgets: freeze(clone(budgets)) }));
}

/** Validate `value` against profile limits, mapping budget failures to `Profile.Exceeded`. */
export function admitData(value, definition) {
	try { validateData(value, definition.limits); }
	catch (error) { if (error instanceof Failure && error.kind === Failure.Budget) throw new Failure(Failure.Budget, Profile.Exceeded); throw error; }
}

// EOF
