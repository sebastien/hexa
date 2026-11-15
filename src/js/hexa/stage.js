/** Module: hexa.stage — Callable generator stages, contracts and tracing. */

import { Checks, Failure, Issue, Runtime, register } from "./core.js";
import { check, compileShape, errors } from "./shape.js";

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

export const STAGES = new WeakMap();
export const ENV_STRANDS = new WeakMap();

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/** Wrap a generator as a callable stage with private metadata. */
export function createStage(fn, combinator = "", details = {}) {
	const metadata = { fn, combinator, name: null, input: null, output: null, explicit: false, ...details };
	const result = function* (env, ...inputs) {
		const strand = ENV_STRANDS.get(env);
		const labels = [metadata.name, metadata.combinator !== metadata.name ? metadata.combinator : null].filter(Boolean);
		strand?.stack.push(...labels);
		try {
			const boundary = strand && strand.stack.length === labels.length;
			const checks = env.policy?.contracts ?? Checks.Boundary;
			if (metadata.explicit && metadata.input && (boundary || checks !== Checks.Off)) check(inputs, metadata.input, Issue.Mismatch);
			const iterator = metadata.fn(env, ...inputs);
			if (!iterator || typeof iterator.next !== "function" || typeof iterator.throw !== "function" || iterator[Symbol.asyncIterator]) throw new Failure(Failure.Defect, Issue.Mismatch);
			const value = yield* iterator;
			if (metadata.explicit && metadata.output && checks !== Checks.Off && errors(value, metadata.output).length) throw new Failure(Failure.Defect, Issue.Mismatch);
			return value;
		} catch (error) {
			const failure = error instanceof Failure ? error : new Failure(Failure.Defect, Issue.Mismatch, { cause: error });
			if (!failure.trace.length && strand) failure.trace = [...strand.stack];
			throw failure;
		} finally {
			if (strand && labels.length) strand.stack.splice(-labels.length);
		}
	};
	Object.defineProperty(result, "name", { value: metadata.name ?? metadata.combinator, configurable: true });
	STAGES.set(result, metadata);
	return result;
}

export function asStage(value) {
	if (STAGES.has(value)) return value;
	if (typeof value !== "function" || value.constructor.name !== "GeneratorFunction") throw new TypeError("stages must be generators; use lift");
	return createStage(value, "", { name: value.name });
}

export function bindStage(value, name) {
	const stage = asStage(value), metadata = STAGES.get(stage);
	if (metadata.name && metadata.name !== name) throw new Failure(Failure.Defect, Runtime.StageRenamed);
	metadata.name = name;
	Object.defineProperty(stage, "name", { value: name, configurable: true });
	register(name, stage);
	return stage;
}

/** Name a computed stage or attach explicit input/output contracts. */
export function stage(...args) {
	const name = typeof args[0] === "string" ? args.shift() : null;
	const contract = args.length > 1 ? args.shift() : {};
	if (args.length !== 1) throw new TypeError("stage expects a generator");
	const source = asStage(args[0]), previous = STAGES.get(source);
	const result = createStage(previous.fn, previous.combinator, {
		...previous,
		input: Object.hasOwn(contract, "input") ? compileShape(contract.input) : previous.input,
		output: Object.hasOwn(contract, "output") ? compileShape(contract.output) : previous.output,
		explicit: previous.explicit || Object.keys(contract).length > 0,
	});
	return name ? bindStage(result, name) : result;
}

// EOF
