/** Module: hexa.core — Symbols, failures, registry and JSON boundaries. */

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

/** @typedef {null|boolean|number|string|JsonValue[]|{[key: string]: JsonValue}} JsonValue */

export const REGISTRY = new Map();
export const ABSENT = Symbol("absent");
export const LIMITS = Object.freeze({ maxDepth: 100, maxNodes: 100000, maxBytes: 10000000 });
export const NAMESPACES = new WeakMap();
export const NAME_RE = /^[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*$(?![\s\S])/;
const RESERVED_MEMBERS = new Set(["name", "members", "unknown", "then", "toJSON", "constructor", "prototype", "__proto__"]);
const UNSAFE_KEYS = new Set(["__proto__", "prototype", "constructor"]);
const MEMBERS = new Set();

/** A value-free pipeline interruption. */
class Failure extends Error {
	static Contract = member("Failure.Contract");
	static Budget = member("Failure.Budget");
	static Cancel = member("Failure.Cancel");
	static Defect = member("Failure.Defect");
	static Shape = member("Failure.Shape");
	static Gap = member("Failure.Gap");
	static Effect = member("Failure.Effect");

	constructor(kind, code, { path, location, cause } = {}) {
		if (!Object.values(Failure).includes(kind) || !MEMBERS.has(code)) throw new TypeError("expected declared failure kind and code");
		super(memberName(code), { cause });
		this.name = "Failure";
		Object.assign(this, { kind, code, path, location, trace: [], fatal: false, exhausted: false, transient: false, issues: [] });
		for (const key of ["ledger", "journal", "cleanup"]) Object.defineProperty(this, key, { value: undefined, writable: true });
	}
}

/** Expected missing or unmappable data. */
export class Gap extends Failure {
	constructor(code, details) {
		super(Failure.Gap, code, details);
		this.name = "Gap";
	}
}

Object.freeze(Failure);
Object.freeze(Gap);

export const Issue = namespace("Issue", "Mismatch Missing Unexpected Constraint CaptureConflict".split(" "));
export const Runtime = namespace("Runtime", "CloseYielded MemberUnknown NameRedeclared NativeAwaitable PathSegmentInvalid ReplayMismatch SlotRedeclared SlotUnknown StageRenamed StrandUnjoined StrandConflict".split(" "));
export const Ledger = namespace("Ledger", ["Gap", "Loss"]);
export const Coverage = namespace("Coverage", "Supplied SourceNull NotSupplied Unmapped Conflicting".split(" "));
export const Checks = namespace("Checks", ["Boundary", "Off"]);
export const Profile = namespace("Profile", ["Exceeded", "Forbidden"]);
const CheckedFailure = new Proxy(Failure, {
	get(target, key) {
		if (key in target) return Reflect.get(target, key);
		if (typeof key === "symbol" || ["then", "toJSON"].includes(key)) return undefined;
		throw new Failure(Failure.Defect, Runtime.MemberUnknown);
	},
});
export { CheckedFailure as Failure };

// Library namespace roots share the registry identity of their members.
for (const [name, value] of Object.entries({ Failure: CheckedFailure, Issue, Runtime, Ledger, Coverage, Checks, Profile })) register(name, value);

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

/** Return the canonical name of an interned member. */
export function memberName(value) {
	return Symbol.keyFor(value)?.replace(/^member:/, "") ?? value.description;
}

export function member(name) {
	const result = Symbol.for(`member:${name}`);
	if (REGISTRY.has(name) && REGISTRY.get(name) !== result) throw new Failure(Failure.Defect, Runtime.NameRedeclared);
	MEMBERS.add(result);
	REGISTRY.set(name, result);
	return result;
}

export function isMember(value) {
	return MEMBERS.has(value);
}

export function isPlain(value) {
	return value !== null && typeof value === "object" && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

export function isThenable(value) {
	return value != null && typeof value.then === "function";
}

export function jsonEqual(left, right) {
	if (left === right) return true;
	if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
	if (Array.isArray(left) !== Array.isArray(right)) return false;
	const keys = Reflect.ownKeys(left);
	return keys.length === Reflect.ownKeys(right).length && keys.every(key => Object.hasOwn(right, key) && jsonEqual(left[key], right[key]));
}

/** Copy data and symbol-keyed metadata without copying program entities. */
export function clone(value) {
	if (Array.isArray(value)) return value.map(clone);
	if (isPlain(value)) return Object.fromEntries(Reflect.ownKeys(value).map(key => [key, clone(value[key])]));
	return value;
}

export function freeze(value) {
	if (Array.isArray(value) || isPlain(value)) {
		for (const child of Object.values(value)) freeze(child);
		Object.freeze(value);
	}
	return value;
}

export function normalise(value) {
	if (typeof value === "number") return Object.is(value, -0) ? 0 : value;
	if (Array.isArray(value)) return value.map(normalise);
	if (isPlain(value)) return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, normalise(child)]));
	return value;
}

/** Spell JSON literals for configuration identities using native JSON number notation. */
export function jsonLiteral(value) {
	return JSON.stringify(value);
}

export function pointer(path) {
	return path.map(part => "/" + String(part).replaceAll("~", "~0").replaceAll("/", "~1")).join("");
}

export function unescapeSegment(segment) {
	return segment.replaceAll("~1", "/").replaceAll("~0", "~");
}

/** Reject non-JSON, cyclic, unsafe or oversized boundary data. */
export function validateData(value, { maxDepth = LIMITS.maxDepth, maxNodes = LIMITS.maxNodes, maxBytes = LIMITS.maxBytes } = {}) {
	const active = new Set();
	let nodes = 0, bytes = 0;
	function visit(item, depth) {
		if (++nodes > maxNodes || depth > maxDepth) throw new Failure(Failure.Budget, Issue.Constraint);
		if (item === null || typeof item === "boolean") bytes += 8;
		else if (typeof item === "number" && Number.isFinite(item)) bytes += 8;
		else if (typeof item === "string") {
			// Reject lone UTF-16 surrogates rather than silently replacing them in UTF-8.
			if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(item)) throw new Failure(Failure.Contract, Issue.Mismatch);
			bytes += new TextEncoder().encode(item).length;
		} else if (Array.isArray(item) || isPlain(item)) {
			if (active.has(item)) throw new Failure(Failure.Contract, Issue.Mismatch);
			active.add(item);
			if (Array.isArray(item)) {
				if (Reflect.ownKeys(item).length !== item.length + 1) throw new Failure(Failure.Contract, Issue.Mismatch);
				for (let index = 0; index < item.length; index++) {
					const descriptor = Object.getOwnPropertyDescriptor(item, index);
					if (!descriptor || !Object.hasOwn(descriptor, "value")) throw new Failure(Failure.Contract, Issue.Mismatch);
					visit(descriptor.value, depth + 1);
				}
			} else {
				for (const key of Reflect.ownKeys(item)) {
					if (typeof key !== "string" || UNSAFE_KEYS.has(key)) throw new Failure(Failure.Contract, Issue.Unexpected);
					const descriptor = Object.getOwnPropertyDescriptor(item, key);
					if (!descriptor.enumerable || !Object.hasOwn(descriptor, "value")) throw new Failure(Failure.Contract, Issue.Mismatch);
					visit(key, depth + 1);
					visit(descriptor.value, depth + 1);
				}
			}
			active.delete(item);
		} else throw new Failure(Failure.Contract, Issue.Mismatch);
		if (bytes > maxBytes) throw new Failure(Failure.Budget, Issue.Constraint);
	}
	visit(value, 0);
}

export function register(name, value) {
	if (!REGISTRY.has(name)) REGISTRY.set(name, value);
	else if (REGISTRY.get(name) !== value && !jsonEqual(REGISTRY.get(name), value)) throw new Failure(Failure.Defect, Runtime.NameRedeclared);
	return REGISTRY.get(name);
}

/** Expose checked immutable members without promise or JSON protocol collisions. */
export function namespace(name, members, unknown) {
	validateMemberNames(Array.isArray(members) ? members : Object.keys(members));
	if (Array.isArray(members)) members = Object.fromEntries(members.map(key => [key, member(`${name}.${key}`)]));
	const target = Object.freeze({ ...members });
	const result = new Proxy(target, {
		get(object, key) {
			if (typeof key === "symbol" || ["then", "toJSON", "constructor"].includes(key)) return undefined;
			if (Object.hasOwn(object, key)) return object[key];
			throw new Failure(Failure.Defect, unknown ?? Runtime.MemberUnknown);
		},
	});
	NAMESPACES.set(result, { name, members: target });
	return result;
}

/** Reject namespace names hidden by either binding's metadata or native protocols. */
export function validateMemberNames(names) {
	for (const name of names) if (RESERVED_MEMBERS.has(name)) throw new TypeError(`reserved namespace member: ${name}`);
}

/** Attach checked reason members to a registered declaration. */
export function withReasons(target, reasons) {
	for (const [key, value] of Object.entries(reasons)) Object.defineProperty(target, key, { value, enumerable: true });
	return new Proxy(Object.freeze(target), {
		get(object, key) {
			if (key in object) return object[key];
			if (typeof key === "symbol" || ["then", "toJSON"].includes(key)) return undefined;
			throw new Failure(Failure.Defect, Runtime.MemberUnknown);
		},
	});
}

export function entityName(value) {
	if (typeof value === "string") return value;
	return NAMESPACES.get(value)?.name ?? value.name;
}

export function failureData(failure) {
	const result = { kind: memberName(failure.kind).split(".").at(-1), code: memberName(failure.code) };
	for (const key of ["path", "location", "trace"]) if (failure[key] !== undefined) result[key] = clone(failure[key]);
	for (const key of ["fatal", "exhausted", "transient"]) if (failure[key]) result[key] = true;
	return result;
}

export function failureFromData(data) {
	if (!Object.hasOwn(Failure, data.kind) || !isMember(REGISTRY.get(data.code))) throw new Failure(Failure.Defect, Runtime.ReplayMismatch);
	const result = new Failure(Failure[data.kind], REGISTRY.get(data.code), data);
	for (const key of ["trace", "fatal", "exhausted", "transient"]) if (Object.hasOwn(data, key)) result[key] = clone(data[key]);
	return result;
}

export function retryable(failure) {
	return failure.kind === Failure.Effect && failure.transient && !failure.fatal && !failure.exhausted;
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/** Build a capped exponential delay in milliseconds. */
export function backoff(milliseconds, { factor = 2, max = 5000 } = {}) {
	if (![milliseconds, factor, max].every(Number.isFinite) || milliseconds < 0 || factor < 1 || max < 0) throw new TypeError("invalid backoff");
	return attempt => Math.min(max, milliseconds * factor ** (attempt - 1));
}

export const DEFAULT_DELAY = backoff(250);

// EOF
