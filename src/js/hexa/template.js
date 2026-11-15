/** Module: hexa.template — Checked derivations and fresh template expansion. */

import { ABSENT, Failure, Gap, Issue, NAME_RE, clone, freeze, namespace, register, withReasons } from "./core.js";
import { Captures, Shape, Slot, capture, captureKey, captureState, checkOutput, children, compatible, compileShape, errors, isCollectionSlot, mergeCaptures } from "./shape.js";

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

export class Transform {
	constructor(definition) {
		Object.assign(this, definition);
	}
}

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

export function compileTemplate(value) {
	if (value instanceof Shape || value instanceof Slot) return compileShape(value);
	if (Array.isArray(value)) {
		if (value.length === 1 && isCollectionSlot(compileShape(value[0]))) return new Shape("list", [new Shape("gather", [compileShape(value[0])])]);
		return new Shape("tuple", value.map(compileTemplate));
	}
	if (value && Object.getPrototypeOf(value) === Object.prototype) return new Shape("struct", Object.entries(value).map(([key, child]) => [key.endsWith("?") ? key.slice(0, -1) : key, key.endsWith("?"), compileTemplate(child)]));
	return compileShape(value);
}

export function validateTemplateReferences(template, partial, quiet = false) {
	if (["optional", "parsed"].includes(template.kind)) return;
	if (["slot", "via"].includes(template.kind) && !quiet && partial.has(template.args[0])) throw new TypeError("partial union slot requires optional");
	if (["struct", "loose"].includes(template.kind)) {
		for (const [, optional, child] of template.args) validateTemplateReferences(child, partial, quiet || optional);
	} else for (const child of children(template)) validateTemplateReferences(child, partial, quiet);
}

export function parameterShapes(parameters, count) {
	const index = parameters.findIndex(shape => shape.kind === "rest");
	if (index < 0) {
		if (parameters.length !== count) throw new TypeError("wrong transform arity");
		return parameters;
	}
	if (count < parameters.length - 1) throw new TypeError("wrong transform arity");
	return [...parameters.slice(0, index), ...Array(count - parameters.length + 1).fill(parameters[index].args[0]), ...parameters.slice(index + 1)];
}

export function expandTemplate(shape, captures, holes, path = [], quiet = false) {
	const { kind, args } = shape;
	if (["struct", "loose"].includes(kind)) {
		const result = {};
		for (const [key, optional, child] of args) {
			const value = expandTemplate(child, captures, holes, [...path, key], quiet || optional);
			if (value !== ABSENT) Object.defineProperty(result, key, { value, enumerable: true, writable: true, configurable: true });
		}
		return result;
	}
	if (["tuple", "list"].includes(kind)) {
		const result = [];
		if (kind === "list" && args[0].kind === "gather") {
			const item = args[0].args[0], key = captureKey(item.args[0]);
			if (!Object.hasOwn(captures, key)) return expandTemplate(compileShape(item.args[0]), captures, holes, path, quiet);
			if (item.kind === "slot") return clone(captures[key]);
			const entry = captureState(captures).evidence.get(key);
			result.length = captures[key].length;
			captures[key].forEach((value, index) => {
				try { result[index] = item.args[1].convert(value, { formatting: true, original: entry?.codec?.[index] === item.args[1].name ? entry.raw[index] : undefined }); }
				catch (error) { if (!(error instanceof Gap)) throw error; holes.push({ path: [...path, index], reason: error.code }); }
			});
			return Object.keys(result).length === result.length ? result : Object.fromEntries(Object.entries(result));
		}
		result.length = args.length;
		args.forEach((child, index) => {
			const value = expandTemplate(child, captures, holes, [...path, index], quiet);
			if (value !== ABSENT) result[index] = value;
		});
		return Object.keys(result).length === result.length ? result : Object.fromEntries(Object.entries(result));
	}
	if (kind === "literal") return clone(args[0]);
	if (["slot", "optional", "via", "parsed"].includes(kind)) {
		const key = captureKey(args[0]), failed = captureState(captures).failed;
		if (!Object.hasOwn(captures, key)) {
			if (failed.has(key) || !quiet && !["optional", "parsed"].includes(kind)) holes.push({ path, reason: failed.get(key) ?? Issue.Missing });
			return ABSENT;
		}
		const entry = captureState(captures).evidence.get(key);
		if (kind === "parsed") return clone(entry?.codec ?? ABSENT);
		if (kind === "via") {
			try { return clone(args[1].convert(captures[key], { formatting: true, original: entry?.codec === args[1].name ? entry.raw : undefined })); }
			catch (error) { if (!(error instanceof Gap)) throw error; holes.push({ path, reason: error.code }); return ABSENT; }
		}
		return clone(captures[key]);
	}
	if (kind === "derive") {
		const [transform, argumentsList] = args, localHoles = [];
		const values = argumentsList.map(argument => expandTemplate(argument, captures, localHoles, path));
		if (localHoles.length) { holes.push(localHoles[0]); return ABSENT; }
		let result;
		try {
			const expected = parameterShapes(transform.inputs, values.length);
			values.forEach((value, index) => { if (value !== ABSENT && errors(value, expected[index]).length) throw new Failure(Failure.Defect, Issue.Mismatch); });
			result = transform.function(...values.map(value => value === ABSENT ? undefined : value));
		} catch (error) {
			if (error instanceof Gap && transform.gaps.includes(error.code)) { holes.push({ path, reason: error.code }); return ABSENT; }
			throw new Failure(Failure.Defect, error instanceof Gap ? Issue.Unexpected : Issue.Mismatch, { cause: error });
		}
		checkOutput(result);
		if (errors(result, transform.output).length) throw new Failure(Failure.Defect, Issue.Mismatch);
		return clone(result);
	}
	throw new TypeError("invalid template");
}

export function outputShape(template) {
	if (["slot", "optional", "parsed"].includes(template.kind)) return template.kind === "parsed" ? compileShape(String) : template.args[0].shape;
	if (template.kind === "via") return template.args[1].source;
	if (template.kind === "derive") return template.args[0].output;
	if (["struct", "loose"].includes(template.kind)) return new Shape(template.kind, template.args.map(([key, optional, child]) => [key, optional || ["optional", "parsed"].includes(child.kind), outputShape(child)]));
	if (["tuple", "list"].includes(template.kind)) {
		if (template.kind === "list" && template.args[0].kind === "gather") {
			const item = template.args[0].args[0];
			return item.kind === "slot" ? item.args[0].shape : new Shape("list", [item.args[1].source]);
		}
		return new Shape(template.kind, template.args.map(outputShape));
	}
	return template;
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/** Declare a checked pure transform. */
export function transform(name, fn, { inputs, output, gaps = [], examples = [] }) {
	if (typeof fn !== "function" || !NAME_RE.test(name) || gaps.some(key => !NAME_RE.test(key) || key.includes("."))) throw new TypeError("invalid transform declaration");
	const parameters = inputs.map(compileShape);
	if (parameters.some((shape, index) => shape.kind === "rest" && index !== parameters.length - 1)) throw new TypeError("variadic parameter must be last");
	const reasons = namespace(name, gaps);
	return register(name, withReasons(new Transform({ name, function: fn, inputs: parameters, output: compileShape(output), gaps: gaps.map(key => reasons[key]), examples: freeze(clone(examples)) }), reasons));
}

/** Apply a declared transform in a template. */
export function derive(transform, ...args) {
	if (!(transform instanceof Transform)) throw new TypeError("expected transform");
	const expected = parameterShapes(transform.inputs, args.length), compiled = args.map(compileTemplate);
	compiled.forEach((item, index) => {
		if (item.kind === "literal" && errors(item.args[0], expected[index]).length) throw new TypeError("invalid derivation literal");
		const actual = ["slot", "optional"].includes(item.kind) ? item.args[0].shape : item.kind === "derive" ? item.args[0].output : null;
		if (actual && !compatible(actual, expected[index])) throw new TypeError("incompatible derivation argument");
	});
	return new Shape("derive", [transform, compiled]);
}

/** Expand into fresh data and a list of missing positions. */
export function expand(template, captures) {
	const holes = [];
	const result = expandTemplate(compileTemplate(template), captures, holes);
	if (result === ABSENT) throw new Gap(holes[0]?.reason ?? Issue.Missing, { path: [] });
	return [result, holes];
}

/** Capture `value` then expand a fresh template. */
export function reshape(shape, template, value, outer = new Captures()) {
	const captures = mergeCaptures(capture(value, shape), outer);
	return expand(template, captures)[0];
}

// EOF
