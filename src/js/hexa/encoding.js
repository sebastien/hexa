/** Module: hexa.encoding — Bounded configuration interchange through a registry. */

import { LIMITS, NAMESPACES, REGISTRY, Failure, Issue, Profile, Runtime, clone, isMember, isPlain, jsonEqual, memberName, validateData } from "./core.js";
import { codec } from "./codec.js";
import { Effect } from "./instructions.js";
import { CONSTRUCTOR_NAMES, ProfileDefinition, RequestFormat, admitData, permissionIssues, profile as declareProfile } from "./profile.js";
import { Codec, Constraint, Shape, Slot, Tuple, compileShape, either, gather, optional, parsed, pattern, refine, rest, tolerant, via } from "./shape.js";
import { STAGES } from "./stage.js";
import { Transform, compileTemplate, derive } from "./template.js";

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

const OPERATOR_OPTIONS = { $: ["via", "optional", "arguments", "value", "stage"], $refine: ["constraints"], $derive: ["arguments"] };
const UNARY = { $tolerant: tolerant, $gather: gather, $parsed: parsed, $rest: rest };

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

/** Escape a `$`-prefixed key or string as verbatim, `$$`. */
function escapeKey(value) { return value.startsWith("$") ? "$" + value : value; }
/** A struct key, `?` marking an optional one. A `$` key is escaped verbatim and a verbatim key is never optional. */
function encodeKey(key, optional) {
	if (optional && key.startsWith("$")) throw new TypeError("an optional key cannot start with '$'");
	return escapeKey(key) + (optional ? "?" : "");
}
/** A plain mapping's key, a trailing `?` marking an optional one, as `encodeKey` spells it. */
function encodeMappingKey(key) { return key.endsWith("?") ? encodeKey(key.slice(0, -1), true) : encodeKey(key, false); }
function short(value) { return memberName(value).split(".").at(-1); }
function encodeCodecReference(value) { return value.family ? { $: value.family.name, arguments: clone(value.arguments) } : value.name; }

function encodeCodec(value) {
	if (value.family) return encodeCodecReference(value);
	const definition = { name: value.name, source: encode(value.source), canonical: encode(value.canonical) };
	if (value.table !== null) Object.assign(definition, { map: clone(value.table), gaps: clone(value.gapTable ?? {}) });
	else Object.assign(definition, { gaps: value.gaps.map(short), losses: value.losses.map(short) });
	if (value.codecFamily) definition.params = Object.fromEntries(value.params.map(([key, shape]) => [key, encode(shape)]));
	if (value.examples.length) definition.examples = clone(value.examples);
	return { $codec: definition };
}

function encodeProfile(value) {
	const definition = { name: value.name };
	if (value.constructors.length) definition.constructors = [...value.constructors];
	for (const key of ["types", "slots", "constraints", "derivations", "codecs"]) {
		if (value[key].length) definition[key] = value[key].map(item => "$" + (key === "types" ? item.args[0] : NAMESPACES.get(item)?.name ?? item.name));
	}
	if (value.shapes.length) definition.shapes = value.shapes.map(([name]) => "$" + name);
	if (value.requests.length) definition.requests = Object.fromEntries(value.requests.map(([item, options]) => [item.name, clone(options)]));
	if (Object.keys(value.budgets).length) definition.budgets = clone(value.budgets);
	return { $profile: definition };
}

function encodeShape(value) {
	const { kind, args } = value;
	if (kind === "type") return "$" + args[0];
	if (["literal", "slot"].includes(kind)) return encode(args[0]);
	if (kind === "parsed") return { $parsed: encode(args[0]) };
	if (kind === "optional") return { $: args[0].name, optional: true };
	if (kind === "via") return { $: args[0].name, via: encodeCodecReference(args[1]) };
	if (["struct", "loose"].includes(kind)) {
		const result = Object.fromEntries(args.map(([key, optional, child]) => [encodeKey(key, optional), encode(child)]));
		return kind === "loose" ? { $loose: result } : result;
	}
	if (kind === "list") return [encode(args[0].kind === "gather" ? args[0].args[0] : args[0])];
	if (["tuple", "either"].includes(kind)) return { ["$" + kind]: args.map(encode) };
	if (kind === "refine") return { $refine: encode(args[0]), constraints: args[1].map(encode) };
	if (kind === "pattern") return { $pattern: args[0] };
	if (kind === "derive") return { $derive: encode(args[0]), arguments: args[1].map(encode) };
	return { ["$" + kind]: encode(args[0]) };
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/** Encode declarations without serializing executable code. */
export function encode(value) {
	if (isMember(value)) return "$" + memberName(value);
	for (const name of ["Policy.Substitute", "Policy.Fallback"]) if (typeof value === "function" && REGISTRY.get(name) === value) return "$" + name;
	if (value instanceof Slot || value instanceof Constraint || value instanceof Transform || value instanceof RequestFormat) return "$" + value.name;
	if (NAMESPACES.has(value)) return Object.fromEntries(Object.entries(NAMESPACES.get(value).members).map(([key, child]) => [key, encode(child)]));
	if (value instanceof Codec || typeof value === "function" && value.codecFamily) return encodeCodec(value);
	if (value instanceof ProfileDefinition) return encodeProfile(value);
	if (value instanceof Shape) return encodeShape(value);
	if (value instanceof Effect) return { operation: value.operation, arguments: clone(value.arguments) };
	if (STAGES.has(value)) {
		const name = STAGES.get(value).name;
		if (!name || REGISTRY.get(name) !== value) throw new TypeError("unnamed stage");
		return "$" + name;
	}
	// Parameterized policies are data records carrying a constructor and payload.
	if (value?.kind && value.kind === REGISTRY.get("Policy.Substitute")) return { $: "Policy.Substitute", value: clone(value.value) };
	if (value?.kind && value.kind === REGISTRY.get("Policy.Fallback")) {
		const name = STAGES.get(value.value)?.name;
		if (!name || REGISTRY.get(name) !== value.value) throw new TypeError("fallback stage must be named");
		return { $: "Policy.Fallback", stage: name };
	}
	if (typeof value === "string") return escapeKey(value);
	if (Array.isArray(value)) return value.map(encode);
	if (isPlain(value)) {
		if (Object.keys(value).length === 2 && Object.hasOwn(value, "operation") && Object.hasOwn(value, "arguments")) { validateData(value); return clone(value); }
		return Object.fromEntries(Object.entries(value).map(([key, child]) => [encodeMappingKey(key), encode(child)]));
	}
	if (value === null || typeof value === "boolean" || typeof value === "number") { validateData(value); return value; }
	throw new TypeError("configuration cannot be encoded");
}

/** Resolve bounded configuration, checking all profile permissions first.
 * `registry` resolves existing declarations; newly decoded codecs and profiles
 * are still registered by their declaration functions in the global registry.
 */
export function decode(value, profile = null, { registry = REGISTRY, ...limits } = {}) {
	if (profile !== null) {
		if (!(profile instanceof ProfileDefinition)) throw new TypeError("expected profile");
		admitData(value, profile);
		const issues = permissionIssues(value, profile, registry);
		if (issues.length) {
			const failure = new Failure(Failure.Contract, Profile.Forbidden, { path: issues[0].path });
			failure.issues = issues;
			throw failure;
		}
	} else validateData(value, { ...LIMITS, ...limits });
	const resolve = name => {
		if (!registry.has(name)) throw new Failure(Failure.Contract, Runtime.MemberUnknown);
		return registry.get(name);
	};
	function visit(item) {
		if (typeof item === "string") return item.startsWith("$$") ? new Shape("literal", [item.slice(1)]) : item.startsWith("$") ? resolve(item.slice(1)) : item;
		if (Array.isArray(item)) return item.map(visit);
		if (!isPlain(item)) return item;
		if (Object.keys(item).length === 2 && Object.hasOwn(item, "operation") && Object.hasOwn(item, "arguments")) return clone(item);
		const operators = Object.keys(item).filter(key => key.startsWith("$") && !key.startsWith("$$"));
		if (!operators.length) {
			if (Object.keys(item).some(key => key.startsWith("$$"))) return new Shape("struct", Object.entries(item).map(([key, child]) => {
				const escaped = key.startsWith("$$"), optionalKey = !escaped && key.endsWith("?");
				const decoded = visit(child);
				return [escaped ? key.slice(1) : optionalKey ? key.slice(0, -1) : key, optionalKey, Array.isArray(decoded) && decoded.length !== 1 ? compileTemplate(decoded) : compileShape(decoded)];
			}));
			return Object.fromEntries(Object.entries(item).map(([key, child]) => [key, visit(child)]));
		}
		if (operators.length !== 1) throw new Failure(Failure.Contract, Issue.Unexpected);
		const operator = operators[0], argument = item[operator];
		if (Object.keys(item).some(key => key !== operator && !(OPERATOR_OPTIONS[operator] ?? []).includes(key))) throw new Failure(Failure.Contract, Issue.Unexpected);
		if (operator === "$") {
			let result = resolve(argument);
			if (Object.hasOwn(item, "value") || Object.hasOwn(item, "stage")) {
				const option = result === REGISTRY.get("Policy.Substitute") ? "value" : result === REGISTRY.get("Policy.Fallback") ? "stage" : null;
				if (!option || !Object.hasOwn(item, option) || Object.keys(item).length !== 2) throw new Failure(Failure.Contract, Issue.Mismatch);
				return result(option === "value" ? item.value : resolve(item.stage));
			}
			if (Object.hasOwn(item, "arguments")) {
				if (typeof result !== "function" || !result.codecFamily || !Array.isArray(item.arguments)) throw new TypeError("expected codec family");
				result = result(...item.arguments);
			}
			if (Object.hasOwn(item, "via")) result = via(result, isPlain(item.via) ? visit(item.via) : resolve(item.via));
			if (item.optional) result = optional(result);
			return result;
		}
		if (operator === "$codec") {
			if (registry.has(argument.name)) {
				const existing = resolve(argument.name);
				if (!(existing instanceof Codec || typeof existing === "function" && existing.codecFamily) || !jsonEqual(encode(existing), item)) throw new Failure(Failure.Contract, Issue.Mismatch);
				return existing;
			}
			if (!Object.hasOwn(argument, "map")) throw new Failure(Failure.Contract, Runtime.MemberUnknown);
			return codec(argument.name, { source: visit(argument.source), canonical: visit(argument.canonical), map: argument.map, gaps: argument.gaps ?? {}, examples: argument.examples ?? [] });
		}
		if (operator === "$profile") {
			if (registry.has(argument.name)) {
				const existing = resolve(argument.name);
				if (!(existing instanceof ProfileDefinition) || !jsonEqual(encode(existing), item)) throw new Failure(Failure.Contract, Issue.Mismatch);
				return existing;
			}
			if (Object.keys(argument).some(key => !["name", "constructors", "types", "shapes", "slots", "constraints", "derivations", "codecs", "requests", "budgets"].includes(key))) throw new Failure(Failure.Contract, Issue.Unexpected);
			const refs = Object.fromEntries(["types", "slots", "constraints", "derivations", "codecs"].map(key => [key, (argument[key] ?? []).map(visit)]));
			return declareProfile(argument.name, {
				...refs, constructors: (argument.constructors ?? []).map(name => CONSTRUCTOR_NAMES.get(name)),
				shapes: Object.fromEntries((argument.shapes ?? []).map(name => [name.slice(1), resolve(name.slice(1))])),
				requests: new Map(Object.entries(argument.requests ?? {}).map(([name, options]) => [resolve(name), options])),
				budgets: argument.budgets ?? {},
			});
		}
		if (["$tuple", "$either"].includes(operator)) return (operator === "$tuple" ? Tuple : either)(...argument.map(visit));
		if (operator === "$loose") {
			const shape = compileShape(visit(argument));
			if (shape.kind !== "struct") throw new TypeError("expected struct");
			return new Shape("loose", shape.args);
		}
		if (operator === "$refine") return refine(visit(argument), ...item.constraints.map(visit));
		if (operator === "$derive") return derive(visit(argument), ...item.arguments.map(visit));
		if (operator === "$pattern") return pattern(argument, { registry });
		if (!Object.hasOwn(UNARY, operator)) throw new Failure(Failure.Contract, Issue.Unexpected);
		return UNARY[operator](visit(argument));
	}
	try { return visit(value); }
	catch (error) {
		if (error instanceof Failure) throw error;
		throw new Failure(Failure.Contract, Issue.Mismatch, { cause: error });
	}
}

/** Describe only the vocabulary a profile permits. */
export function describe(profile) {
	if (!(profile instanceof ProfileDefinition)) throw new TypeError("expected profile");
	return {
		constructors: [...profile.constructors], types: profile.types.map(encode),
		shapes: Object.fromEntries(profile.shapes.map(([name, shape]) => [name, encode(shape)])),
		slots: Object.fromEntries(profile.slots.flatMap(item => Object.values(NAMESPACES.get(item)?.members ?? { slot: item })).map(item => [item.name, encode(item.shape)])),
		constraints: profile.constraints.map(encode),
		derivations: Object.fromEntries(profile.derivations.map(item => [item.name, { inputs: item.inputs.map(encode), output: encode(item.output), gaps: item.gaps.map(short), ...(item.examples.length ? { examples: clone(item.examples) } : {}) }])),
		codecs: Object.fromEntries(profile.codecs.map(item => [item.name, encodeCodec(item)])),
		requests: Object.fromEntries(profile.requests.map(([item, options]) => [item.name, { gaps: item.gaps.map(short), ...clone(options) }])),
	};
}

// EOF
