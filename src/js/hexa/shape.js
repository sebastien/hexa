/** Module: hexa.shape — Shape algebra, matching, captures and codec contracts. */

import { NAME_RE, REGISTRY, Failure, Gap, Issue, Runtime, clone, freeze, isMember, isPlain, isThenable, jsonEqual, member, namespace, normalise, pointer, register, validateData, validateMemberNames } from "./core.js";

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

/** @typedef {{kind: string, args: Array}} ShapeData */

export const SLOTS = new Map();
const CAPTURES = new WeakMap();
const DATE_RE = /^([0-9]{4}-[0-9]{2}-[0-9]{2})(?:T([0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?)(Z|[+-][0-9]{2}:[0-9]{2})?)?$/;

/** Immutable compiled algebra node. */
export class Shape {
	constructor(kind, args = []) {
		this.kind = kind;
		this.args = freeze(args);
		Object.freeze(this);
	}
}

/** Interned typed capture point. */
export class Slot {
	constructor(name, shape) {
		Object.assign(this, { name, shape, symbol: Symbol.for(`slot:${name}`) });
		Object.freeze(this);
	}
}

export class Constraint {
	constructor(name, predicate) {
		Object.assign(this, { name, predicate });
		Object.freeze(this);
	}
}

export class Lossy {
	constructor(value, reason) {
		Object.assign(this, { value, reason });
	}
}

/** Capture data; local string keys never share storage with engine state. */
export class Captures {
	constructor() {
		captureState(this);
	}
}

/** Concrete source/canonical conversion with checked pure callbacks. */
export class Codec {
	constructor(definition) {
		Object.assign(this, definition);
	}

	convert(value, { formatting = false, original } = {}) {
		if (errors(value, formatting ? this.canonical : this.source).length) throw new Failure(Failure.Defect, Issue.Mismatch);
		let result;
		try {
			result = formatting ? this.format(...clone(this.arguments), clone(value), clone(original)) : this.parse(...clone(this.arguments), clone(value));
		} catch (error) {
			if (error instanceof Gap && this.gaps.includes(error.code)) throw error;
			throw new Failure(Failure.Defect, error instanceof Gap ? Issue.Unexpected : Issue.Mismatch, { cause: error });
		}
		if (isThenable(result)) throw new Failure(Failure.Defect, Runtime.NativeAwaitable);
		if (result instanceof Lossy && (formatting || !this.losses.includes(result.reason))) throw new Failure(Failure.Defect, Issue.Unexpected);
		const actual = result instanceof Lossy ? result.value : result;
		checkOutput(actual);
		if (errors(actual, formatting ? this.source : this.canonical).length) throw new Failure(Failure.Defect, Issue.Mismatch);
		return result;
	}
}

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

export function captureKey(value) {
	return value instanceof Slot ? value.symbol : value;
}

/** Keep metadata outside the user-visible string and slot capture keys. */
export function captureState(captures) {
	if (!CAPTURES.has(captures)) CAPTURES.set(captures, { evidence: new Map(), failed: new Map(), losses: [], gathered: new Set() });
	return CAPTURES.get(captures);
}

export function copyCaptures(captures) {
	const result = new Captures(), state = captureState(captures);
	for (const key of Reflect.ownKeys(captures)) result[key] = clone(captures[key]);
	CAPTURES.set(result, {
		evidence: new Map([...state.evidence].map(([key, value]) => [key, clone(value)])),
		failed: new Map(state.failed), losses: state.losses.map(clone), gathered: new Set(state.gathered),
	});
	return result;
}

export function replaceCaptures(target, source) {
	for (const key of Reflect.ownKeys(target)) delete target[key];
	Object.assign(target, source);
	Object.assign(captureState(target), captureState(source));
}

export function checkOutput(value) {
	if (isThenable(value)) throw new Failure(Failure.Defect, Runtime.NativeAwaitable);
	try { validateData(value); }
	catch (error) {
		throw new Failure(Failure.Defect, error instanceof Failure ? error.code : Issue.Mismatch, { cause: error });
	}
}

export function daysInMonth(year, month) {
	return [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
}

export function dateParts(value) {
	const found = typeof value === "string" && DATE_RE.exec(value);
	if (!found || found[0] !== value) return null;
	const [, date, time = null, zone = null] = found;
	const [year, month, day] = date.split("-").map(Number);
	if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month)) return null;
	if (time && (+time.slice(0, 2) > 23 || +time.slice(3, 5) > 59 || (time.length > 5 && +time.slice(6, 8) > 59))) return null;
	if (zone && zone !== "Z" && (+zone.slice(1, 3) > 23 || +zone.slice(4) > 59)) return null;
	return { date, time, zone };
}

export function typeMatches(value, name) {
	switch (name) {
		case "Any":
			try { validateData(value); return true; }
			catch (error) { if (error instanceof Failure) return false; throw error; }
		case "Text": return typeof value === "string";
		case "Bool": return typeof value === "boolean";
		case "Null": return value === null;
		case "Struct": return isPlain(value) && Reflect.ownKeys(value).every(key => typeof key === "string" && !["__proto__", "prototype", "constructor"].includes(key) && Object.hasOwn(Object.getOwnPropertyDescriptor(value, key), "value"));
		case "List": return Array.isArray(value) && Array.from({ length: value.length }, (_, index) => Object.hasOwn(Object.getOwnPropertyDescriptor(value, index) ?? {}, "value")).every(Boolean);
		case "Num": return typeof value === "number" && Number.isFinite(value);
		case "Int": return Number.isSafeInteger(value);
		case "Float": return typeof value === "number" && Number.isFinite(value) && !Number.isInteger(value);
		case "DateTime": return dateParts(value) !== null;
		case "Instant": return dateParts(value) !== null && /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{0,8}[1-9])?Z$/.test(value);
		case "Decimal": return typeof value === "string" && value !== "-0" && /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$(?![\s\S])/.test(value);
		default: return false;
	}
}

export function compatible(source, target) {
	if (target.kind === "type" && target.args[0] === "Any" || jsonEqual(source, target)) return true;
	if (source.kind === "literal") return errors(source.args[0], target).length === 0;
	if (source.kind === "either") return source.args.every(child => compatible(child, target));
	if (target.kind === "either") return target.args.some(child => compatible(source, child));
	if (target.kind === "refine") return source.kind === "refine" && compatible(source.args[0], target.args[0]) && target.args[1].every(constraint => source.args[1].includes(constraint));
	if (source.kind === "refine") return compatible(source.args[0], target);
	if (source.kind === "list" && target.kind === "list") return compatible(source.args[0], target.args[0]);
	if (source.kind === "type" && target.kind === "type") {
		return ({ Int: ["Num"], Float: ["Num"], Instant: ["DateTime"] }[source.args[0]] ?? []).includes(target.args[0]);
	}
	if (["struct", "loose"].includes(source.kind) && ["struct", "loose"].includes(target.kind)) {
		for (const [key, optional, child] of target.args) {
			const field = source.args.find(item => item[0] === key);
			if (!field) { if (!optional) return false; }
			else if (field[1] && !optional || !compatible(field[2], child)) return false;
		}
		return target.kind === "loose" || source.kind === "struct" && source.args.every(([key]) => target.args.some(item => item[0] === key));
	}
	return false;
}

export function isCollectionSlot(shape) {
	return ["slot", "via"].includes(shape.kind) && shape.args[0].shape.kind === "list";
}

export function compileShape(value) {
	if (value instanceof Shape) return value;
	if (value instanceof Slot) return new Shape("slot", [value]);
	const native = new Map([[String, Text], [Number, Num], [Boolean, Bool], [Object, Struct], [Array, List]]);
	if (native.has(value)) return native.get(value);
	if (value instanceof Set) return either(...value);
	if (isPlain(value)) return new Shape("struct", Object.entries(value).map(([key, child]) => [key.endsWith("?") ? key.slice(0, -1) : key, key.endsWith("?"), compileShape(child)]));
	if (Array.isArray(value)) {
		if (value.length !== 1 || !Object.hasOwn(value, 0)) throw new TypeError("list shapes have one item; use Tuple");
		const item = compileShape(value[0]);
		return new Shape("list", [isCollectionSlot(item) ? new Shape("gather", [item]) : item]);
	}
	if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
		validateData(value);
		return new Shape("literal", [value]);
	}
	throw new TypeError("unsupported shape");
}

export function children(shape) {
	if (["struct", "loose"].includes(shape.kind)) return shape.args.map(item => item[2]);
	if (["tuple", "either"].includes(shape.kind)) return shape.args;
	if (["list", "rest", "tolerant", "gather", "refine"].includes(shape.kind)) return [shape.args[0]];
	if (shape.kind === "derive") return shape.args[1];
	return [];
}

export function references(shape) {
	const result = new Set();
	if (["slot", "via", "optional", "parsed"].includes(shape.kind)) result.add(shape.args[0]);
	if (shape.kind === "pattern") for (const [key] of shape.args[2]) result.add(key);
	for (const child of children(shape)) for (const key of references(child)) result.add(key);
	return result;
}

export function definiteReferences(shape) {
	if (shape.kind === "either") {
		const sets = shape.args.map(definiteReferences);
		return new Set([...sets[0]].filter(key => sets.every(set => set.has(key))));
	}
	if (["slot", "via", "pattern"].includes(shape.kind)) return references(shape);
	return new Set(children(shape).flatMap(child => [...definiteReferences(child)]));
}

export function validateShape(shape, inList = false, gathers = new Set(), allowRest = false, itemMode = false) {
	if (["optional", "derive", "parsed"].includes(shape.kind)) throw new TypeError("template operator used in a shape");
	if (shape.kind === "rest" && !allowRest) throw new TypeError("rest requires Tuple");
	if (shape.kind === "via") {
		let expected = shape.args[0].shape;
		if (itemMode && expected.kind === "list") expected = expected.args[0];
		if (!compatible(shape.args[1].canonical, expected)) throw new TypeError("incompatible codec");
	}
	if (shape.kind === "gather" || shape.kind === "rest" && isCollectionSlot(shape.args[0])) {
		if (shape.kind === "gather" && !inList) throw new TypeError("gather requires a list");
		const key = shape.args[0].args[0];
		if (gathers.has(key)) throw new TypeError("collection gathered more than once");
		gathers.add(key);
	}
	for (const child of children(shape)) validateShape(child, inList || ["list", "rest"].includes(shape.kind), gathers, shape.kind === "tuple", ["gather", "rest"].includes(shape.kind));
}

export function mergeCaptures(target, source) {
	const targetState = captureState(target), sourceState = captureState(source);
	for (const key of Reflect.ownKeys(source)) {
		if (!Object.getOwnPropertyDescriptor(source, key).enumerable) continue;
		if (Object.hasOwn(target, key) && !jsonEqual(target[key], source[key])) throw new Failure(Failure.Shape, Issue.CaptureConflict);
		if (!Object.hasOwn(target, key)) {
			target[key] = clone(source[key]);
			if (sourceState.evidence.has(key)) targetState.evidence.set(key, clone(sourceState.evidence.get(key)));
		}
	}
	for (const [key, reason] of sourceState.failed) if (!Object.hasOwn(target, key)) targetState.failed.set(key, reason);
	targetState.losses.push(...sourceState.losses);
	return target;
}

function startGather(captures, slot) {
	const key = captureKey(slot);
	captures[key] = [];
	captureState(captures).gathered.add(key);
	captureState(captures).evidence.set(key, { raw: [], location: [], codec: [] });
}

function gatherReferences(shape) {
	if (shape.kind === "gather") return [shape.args[0].args[0]];
	return children(shape).flatMap(gatherReferences);
}

function gatherItem(shape) {
	return isCollectionSlot(shape) ? new Shape("gather", [shape]) : shape;
}

function addIssue(issues, code, path, extra = {}) {
	issues.push({ code, path, ...extra });
}

function bindCapture(captures, slot, value, raw, path, issues, codec, gathering) {
	const key = captureKey(slot);
	const state = captureState(captures);
	if (gathering && state.failed.has(key)) return;
	value = normalise(value);
	if (gathering) {
		if (!Object.hasOwn(captures, key)) startGather(captures, slot);
		captures[key].push(clone(value));
		const entry = state.evidence.get(key);
		entry.raw.push(clone(raw));
		entry.location.push(pointer(path));
		entry.codec.push(codec?.name ?? null);
	} else if (Object.hasOwn(captures, key) && !jsonEqual(captures[key], value)) addIssue(issues, Issue.CaptureConflict, path);
	else if (!Object.hasOwn(captures, key)) {
		captures[key] = clone(value);
		state.evidence.set(key, { raw: clone(raw), location: pointer(path), ...(codec ? { codec: codec.name } : {}) });
	}
}

// -----------------------------------------------------------------------------
//
// OPERATIONS
//
// -----------------------------------------------------------------------------

// NOTE: Matchers share one signature so `matchShape` can dispatch on `shape.kind`.

function matchType(value, shape, captures, issues, path, gathering) {
	if (!typeMatches(value, shape.args[0])) addIssue(issues, Issue.Mismatch, path);
}

function matchLiteral(value, shape, captures, issues, path, gathering) {
	if (!jsonEqual(value, shape.args[0])) addIssue(issues, Issue.Mismatch, path);
}

function matchStruct(value, shape, captures, issues, path, gathering) {
	const { kind, args } = shape;
	if (!typeMatches(value, "Struct")) { addIssue(issues, Issue.Mismatch, path); return; }
	for (const [key, optional, child] of args) {
		if (!Object.hasOwn(value, key) || optional && value[key] === undefined) { if (!optional) addIssue(issues, Issue.Missing, [...path, key]); }
		else matchShape(value[key], child, captures, issues, [...path, key]);
	}
	if (kind === "struct") for (const key of Object.keys(value)) if (!args.some(item => item[0] === key)) addIssue(issues, Issue.Unexpected, [...path, key]);
}

function matchList(value, shape, captures, issues, path, gathering) {
	const state = captureState(captures);
	if (!typeMatches(value, "List")) { addIssue(issues, Issue.Mismatch, path); return; }
	const child = shape.kind === "rest" ? gatherItem(shape.args[0]) : shape.args[0];
	for (const slot of gatherReferences(child)) if (!state.gathered.has(captureKey(slot))) startGather(captures, slot);
	value.forEach((item, index) => matchShape(item, child, captures, issues, [...path, index]));
}

function matchTuple(value, shape, captures, issues, path, gathering) {
	const { args } = shape;
	const restIndex = args.findIndex(item => item.kind === "rest");
	if (!typeMatches(value, "List") || (restIndex < 0 ? value.length !== args.length : value.length < args.length - 1)) { addIssue(issues, Issue.Mismatch, path); return; }
	args.forEach((child, index) => {
		if (index === restIndex) {
			const item = gatherItem(child.args[0]);
			for (const slot of gatherReferences(item)) startGather(captures, slot);
			for (let offset = index; offset < value.length - (args.length - index - 1); offset++) matchShape(value[offset], item, captures, issues, [...path, offset]);
		} else {
			const position = restIndex < 0 || index < restIndex ? index : value.length - args.length + index;
			matchShape(value[position], child, captures, issues, [...path, position]);
		}
	});
}

function matchEither(value, shape, captures, issues, path, gathering) {
	const failures = [];
	for (const child of shape.args) {
		const trial = copyCaptures(captures), inner = [];
		try { matchShape(value, child, trial, inner, path); }
		catch (error) { if (!(error instanceof Gap)) throw error; failures.push(error); continue; }
		if (!inner.length) { replaceCaptures(captures, trial); return; }
		failures.push(inner);
	}
	if (failures.every(item => item instanceof Gap)) throw failures[0];
	addIssue(issues, Issue.Mismatch, path);
}

function matchRefine(value, shape, captures, issues, path, gathering) {
	const before = issues.length;
	matchShape(value, shape.args[0], captures, issues, path);
	if (issues.length !== before) return;
	for (const constraint of shape.args[1]) {
		let valid;
		try { valid = constraint.predicate(value); }
		catch (cause) { throw new Failure(Failure.Defect, Issue.Constraint, { path, cause }); }
		if (isThenable(valid)) throw new Failure(Failure.Defect, Runtime.NativeAwaitable);
		if (!valid) { addIssue(issues, Issue.Constraint, path, { constraint: constraint.name }); break; }
	}
}

function matchTolerant(value, shape, captures, issues, path, gathering) {
	const state = captureState(captures);
	const trial = copyCaptures(captures), inner = [];
	let reason;
	try { matchShape(value, shape.args[0], trial, inner, path); reason = inner[0]?.code; }
	catch (error) { if (!(error instanceof Gap)) throw error; reason = error.code; }
	if (!reason) { replaceCaptures(captures, trial); return; }
	for (const slot of references(shape.args[0])) {
		const key = captureKey(slot);
		if (!Object.hasOwn(captures, key) || state.gathered.has(key)) {
			state.failed.set(key, reason);
			delete captures[key];
			state.evidence.delete(key);
			state.losses = state.losses.filter(loss => loss.target !== (slot.name ?? slot));
		}
	}
}

function matchGather(value, shape, captures, issues, path, gathering) {
	matchShape(value, shape.args[0], captures, issues, path, true);
}

function matchSlot(value, shape, captures, issues, path, gathering) {
	const state = captureState(captures);
	const [slot, codec] = shape.args;
	const expected = gathering ? slot.shape.args[0] : slot.shape;
	const before = issues.length;
	matchShape(value, codec ? codec.source : expected, captures, issues, path);
	if (issues.length !== before) return;
	let canonical = value;
	if (codec) {
		try { canonical = codec.convert(value); }
		catch (error) { if (error instanceof Gap) error.path = path; throw error; }
		if (canonical instanceof Lossy) {
			state.losses.push({ target: slot.name, reason: canonical.reason, path });
			canonical = canonical.value;
		}
		matchShape(canonical, expected, captures, issues, path);
	}
	if (issues.length === before) bindCapture(captures, slot, canonical, value, path, issues, codec, gathering);
}

function matchPattern(value, shape, captures, issues, path, gathering) {
	const state = captureState(captures);
	const found = typeof value === "string" && shape.args[1].exec(value);
	if (!found) { addIssue(issues, Issue.Mismatch, path); return; }
	shape.args[2].forEach(([key, codec], index) => {
		const raw = found[index + 1];
		if (key instanceof Slot) matchShape(raw, codec ? via(key, codec) : compileShape(key), captures, issues, path);
		else {
			let result = codec ? codec.convert(raw) : raw;
			if (result instanceof Lossy) { state.losses.push({ target: key, reason: result.reason, path }); result = result.value; }
			bindCapture(captures, key, result, raw, path, issues, codec, false);
		}
	});
}

const MATCHERS = new Map([
	["type", matchType], ["literal", matchLiteral], ["struct", matchStruct], ["loose", matchStruct],
	["list", matchList], ["rest", matchList], ["tuple", matchTuple], ["either", matchEither],
	["refine", matchRefine], ["tolerant", matchTolerant], ["gather", matchGather],
	["slot", matchSlot], ["via", matchSlot], ["pattern", matchPattern],
]);

/** Match one node, accumulating captures and value-free structural issues. */
export function matchShape(value, shape, captures, issues, path = [], gathering = false) {
	captureState(captures);
	const matcher = MATCHERS.get(shape.kind);
	if (!matcher) throw new TypeError("invalid matching operator");
	matcher(value, shape, captures, issues, path, gathering);
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

export const [Any, Text, Num, Int, Float, Bool, Null, Struct, List, DateTime, Instant, Decimal] =
	"Any Text Num Int Float Bool Null Struct List DateTime Instant Decimal".split(" ").map(name => register(name, new Shape("type", [name])));

/** Declare a checked namespace of interned reason codes. */
export function codes(name, ...names) {
	if (!NAME_RE.test(name) || names.some(item => !NAME_RE.test(item) || item.includes("."))) throw new TypeError("invalid code name");
	return register(name, namespace(name, names));
}

/** Declare one typed, interned slot. */
export function slot(name, shape) {
	if (!NAME_RE.test(name)) throw new TypeError("invalid slot name");
	const compiled = compileShape(shape);
	validateShape(compiled);
	if (SLOTS.has(name)) {
		if (!jsonEqual(SLOTS.get(name).shape, compiled)) throw new Failure(Failure.Defect, Runtime.SlotRedeclared);
		return SLOTS.get(name);
	}
	const result = register(name, new Slot(name, compiled));
	SLOTS.set(name, result);
	return result;
}

/** Declare a frozen namespace of typed capture points. */
export function slots(name, shapes) {
	validateMemberNames(Object.keys(shapes));
	return register(name, namespace(name, Object.fromEntries(Object.entries(shapes).map(([key, value]) => [key, slot(`${name}.${key}`, value)])), Runtime.SlotUnknown));
}

/** Match fixed positions with at most one variable-length rest. */
export function Tuple(...shapes) {
	const result = new Shape("tuple", shapes.map(compileShape));
	if (result.args.filter(item => item.kind === "rest").length > 1) throw new TypeError("multiple rests");
	return result;
}

/** Match zero or more tuple positions against `shape`. */
export function rest(shape) { return new Shape("rest", [compileShape(shape)]); }
export const __ = rest(Any);
/** Match declared fields while allowing additional keys. */
export function loose(shape) {
	const compiled = compileShape(shape);
	if (compiled.kind !== "struct") throw new TypeError("loose requires struct");
	return new Shape("loose", compiled.args);
}
/** Choose the first successful alternative without leaking failed captures. */
export function either(...shapes) {
	if (!shapes.length) throw new TypeError("either requires alternatives");
	return new Shape("either", shapes.map(compileShape));
}
/** Admit explicit null in addition to `shape`. */
export function nullable(shape) { return either(shape, Null); }
/** Register a pure `predicate` as the named constraint `name`. */
export function where(predicate, name) {
	if (typeof name !== "string" || !NAME_RE.test(name) || typeof predicate !== "function") throw new TypeError("invalid constraint");
	return register(name, new Constraint(name, predicate));
}
/** Apply named constraints after structural matching. */
export function refine(shape, ...constraints) {
	if (!constraints.length || constraints.some(item => !(item instanceof Constraint))) throw new TypeError("expected constraints");
	const compiled = compileShape(shape);
	return compiled.kind === "refine" ? new Shape("refine", [compiled.args[0], [...compiled.args[1], ...constraints]]) : new Shape("refine", [compiled, constraints]);
}
/** Attach a concrete source codec to a slot or formatting position. */
export function via(slot, codec) {
	if (!(slot instanceof Slot) || !(codec instanceof Codec)) throw new TypeError("via requires slot and concrete codec");
	return new Shape("via", [slot, codec]);
}
/** Suppress expected absence of an otherwise valid template slot. */
export function optional(slot) {
	if (!(slot instanceof Slot)) throw new TypeError("optional requires slot");
	return new Shape("optional", [slot]);
}
/** Expand to the name of the codec used to capture `slot`, when present. */
export function parsed(slot) {
	if (!(slot instanceof Slot)) throw new TypeError("parsed requires slot");
	return new Shape("parsed", [slot]);
}
/** Isolate a failed position and retain its reason for later template holes. */
export function tolerant(shape) { return new Shape("tolerant", [compileShape(shape)]); }
/** Gather one item into a collection slot within an enclosing list. */
export function gather(slot) {
	const compiled = compileShape(slot);
	if (!isCollectionSlot(compiled)) throw new TypeError("gather requires collection slot");
	return new Shape("gather", [compiled]);
}
/** Match a complete string with local or qualified capture placeholders. */
export function pattern(source, { registry = REGISTRY } = {}) {
	if (typeof source !== "string") throw new TypeError("pattern requires text");
	const captures = [];
	let regex = "", offset = 0;
	const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
	for (const found of source.matchAll(/\{([^{}]+)\}/g)) {
		const literal = source.slice(offset, found.index);
		if (/[{}]/.test(literal)) throw new TypeError("invalid pattern");
		regex += escape(literal);
		const [name, codecName, extra] = found[1].split(":");
		if (!NAME_RE.test(name) || extra) throw new TypeError("invalid capture");
		const key = name.includes(".") || ["Location", "Origin"].includes(name) ? registry.get(name) : name;
		const codec = codecName ? registry.get(codecName) : undefined;
		if (!(key instanceof Slot) && typeof key !== "string") throw new Failure(Failure.Contract, Runtime.SlotUnknown);
		if (codecName && !(codec instanceof Codec)) throw new Failure(Failure.Contract, Runtime.MemberUnknown);
		if (key instanceof Slot && !compatible(codec?.canonical ?? Text, key.shape)) throw new TypeError("incompatible pattern slot");
		captures.push([key, codec]);
		regex += "(.*?)";
		offset = found.index + found[0].length;
	}
	if (/[{}]/.test(source.slice(offset))) throw new TypeError("invalid pattern");
	return new Shape("pattern", [source, new RegExp(`^(?:${regex + escape(source.slice(offset))})$(?![\\s\\S])`, "s"), captures]);
}
/** Return a canonical value carrying a declared information-loss reason. */
export function lossy(value, reason) {
	if (!isMember(reason)) throw new TypeError("expected loss code");
	return new Lossy(value, reason);
}
/** Return structural issues without source values. */
export function errors(value, shape) {
	const compiled = compileShape(shape);
	validateShape(compiled);
	const issues = [];
	matchShape(value, compiled, new Captures(), issues);
	return issues;
}
/** Return `value` or throw a contract failure. */
export function check(value, shape, code) {
	let issues;
	try { issues = errors(value, shape); }
	catch (error) { if (!(error instanceof Gap)) throw error; throw new Failure(Failure.Contract, code, { path: error.path }); }
	if (issues.length) {
		const failure = new Failure(Failure.Contract, code, { path: issues[0].path });
		failure.issues = issues;
		throw failure;
	}
	return value;
}
/** Match and parse `value` into symbol-keyed captures. */
export function capture(value, shape) {
	const compiled = compileShape(shape);
	validateShape(compiled);
	const result = new Captures(), issues = [];
	matchShape(value, compiled, result, issues);
	if (issues.length) {
		const failure = new Failure(Failure.Shape, issues[0].code, { path: issues[0].path });
		failure.issues = issues;
		throw failure;
	}
	return result;
}
/** Return detached source evidence for `slot`. */
export function evidence(captures, slot) {
	const result = clone(captureState(captures).evidence.get(captureKey(slot)) ?? null);
	if (Array.isArray(result?.codec) && !result.codec.some(Boolean)) delete result.codec;
	return result;
}

// EOF
