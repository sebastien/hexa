/** Module: hexa.combinators — Declarative stage composition and failure policies. */

import { ABSENT, DEFAULT_DELAY, Failure, Gap, Issue, Ledger, Profile, Runtime, clone, entityName, failureData, freeze, jsonEqual, member, memberName, namespace, register, retryable, validateData, validateMemberNames } from "./core.js";
import { LedgerRead, Parallel, Report } from "./instructions.js";
import { matches } from "./selector.js";
import { Slot, capture, captureKey, captureState, check, compileShape, definiteReferences, errors, mergeCaptures, references, validateShape } from "./shape.js";
import { ENV_STRANDS, STAGES, asStage, bindStage, createStage } from "./stage.js";
import { compileTemplate, expand, outputShape, validateTemplateReferences } from "./template.js";
import { Location, Origin } from "./vocabulary.js";
import { RequestFormat, admitData } from "./profile.js";

export { stage } from "./stage.js";
export { backoff } from "./core.js";

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

class PolicyValue {
	constructor(kind, value) {
		Object.assign(this, { kind, value });
		Object.freeze(this);
	}
}

const Substitute = value => { validateData(value); return new PolicyValue(Substitute, freeze(clone(value))); };
const Fallback = stage => new PolicyValue(Fallback, asStage(stage));
register("Policy.Substitute", Substitute);
register("Policy.Fallback", Fallback);
export const Policy = register("Policy", namespace("Policy", { Abort: member("Policy.Abort"), Drop: member("Policy.Drop"), Substitute, Fallback }));
const RECOVERABLE = [Failure.Shape, Failure.Gap, Failure.Effect];

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

function isOccurrence(value) {
	return value && typeof value === "object" && Object.hasOwn(value, "value") && Object.hasOwn(value, "location");
}

function reportTarget(env, explicit) {
	if (explicit !== undefined) {
		if (typeof explicit === "string" || !entityName(explicit)) throw new TypeError("target must be program entity");
		return explicit;
	}
	const stack = ENV_STRANDS.get(env)?.stack ?? [];
	return [...stack].reverse().find(name => /^[A-Z]/.test(name)) ?? stack.at(-1);
}

function validateOptions(options) {
	if (Object.keys(options).some(key => !["on", "target", "reason"].includes(key)) || options.on?.some(kind => !Object.values(Failure).includes(kind))) throw new TypeError("invalid policy options");
}

function* applyPolicy(env, source, inputs, policy, options, allowDrop = false) {
	try { return yield* source(env, ...inputs); }
	catch (failure) {
		// Fatal and exhausted failures cannot be recovered by an outer policy.
		if (!(failure instanceof Failure) || !RECOVERABLE.includes(failure.kind) || !(options.on ?? [Failure.Shape, Failure.Gap]).includes(failure.kind) || failure.fatal || failure.exhausted) throw failure;
		const reason = typeof options.reason === "function" ? options.reason(failure) : options.reason ?? failure.code;
		const fields = { target: reportTarget(env, options.target), reason };
		const location = failure.location ?? (isOccurrence(inputs[0]) ? inputs[0].location : undefined);
		if (location !== undefined) fields.source = location;
		if (failure.path !== undefined) fields.path = failure.path;
		if (policy === Policy.Abort) fields.fatal = true;
		yield new Report(Ledger.Gap, fields);
		if (policy === Policy.Abort) { failure.fatal = true; failure.code = reason; throw failure; }
		if (policy === Policy.Drop && allowDrop) return ABSENT;
		if (policy instanceof PolicyValue) {
			if (policy.kind === Substitute) {
				const value = clone(policy.value), output = STAGES.get(source).output;
				if (output && errors(value, output).length) throw new Failure(Failure.Defect, Issue.Mismatch);
				return value;
			}
			return yield* policy.value(env, ...inputs);
		}
		throw new TypeError("invalid failure policy");
	}
}

function requestOptions(env, format) {
	if (!(format instanceof RequestFormat)) throw new TypeError("expected request format");
	const definition = env.policy.profile, options = definition?.options(format);
	if (!options) throw new Failure(Failure.Contract, Profile.Forbidden);
	return [definition, options];
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/** Bind named declarations and resolve every late reference before returning. */
export function stages(declare) {
	const requested = new Map();
	let resolved;
	const references = new Proxy({}, {
		get(target, key) {
			// Do not let promise or serialization probes create late references.
			if (typeof key === "symbol" || ["then", "toJSON", "constructor"].includes(key)) return undefined;
			if (resolved) return resolved[key];
			if (!requested.has(key)) {
				const reference = createStage(function* (env, ...inputs) { return yield* resolved[key](env, ...inputs); });
				requested.set(key, reference);
			}
			return requested.get(key);
		},
	});
	const declarations = declare(references), members = {};
	validateMemberNames(Object.keys(declarations));
	for (const [key, value] of Object.entries(declarations)) members[key] = bindStage(value, key);
	for (const key of requested.keys()) if (!Object.hasOwn(members, key)) throw new Failure(Failure.Defect, Runtime.MemberUnknown);
	resolved = namespace("stages", members);
	return resolved;
}

/** Wrap a pure `fn` as a generator stage. */
export function lift(fn) {
	return createStage(function* (env, ...inputs) { return yield fn(...inputs); }, "lift");
}

/** Pass each stage's result to the next stage. */
export function pipe(...sources) {
	if (!sources.length) throw new TypeError("pipe requires stages");
	sources = sources.map(asStage);
	return createStage(function* (env, ...inputs) {
		let value = yield* sources[0](env, ...inputs);
		for (const source of sources.slice(1)) value = yield* source(env, value);
		return value;
	}, "pipe", { input: STAGES.get(sources[0]).input, output: STAGES.get(sources.at(-1)).output });
}

/** Check a fixed or environment-derived `shape` without changing the value. */
export function guard(shape, code) {
	return createStage(function* (env, value) { return yield check(value, typeof shape === "function" ? shape(env) : shape, code); }, "guard", { output: typeof shape === "function" ? null : compileShape(shape) });
}

/** Select located occurrences and attach known effect origins. */
export function select(selector) {
	return createStage(function* (env, value) {
		const occurrences = matches(value, selector), strand = ENV_STRANDS.get(env);
		for (const occurrence of occurrences) {
			const origin = strand?.origin(value, occurrence.location);
			if (origin) occurrence.origin = origin;
		}
		return yield occurrences;
	}, "select");
}

/** Capture a value or occurrence, expand `template` and report local holes. */
export function project(shape, template, { outer = [], holes = Policy.Drop } = {}) {
	shape = compileShape(shape);
	template = compileTemplate(template);
	validateShape(shape);
	if (![Policy.Drop, Policy.Abort].includes(holes)) throw new TypeError("invalid holes policy");
	const supplied = references(shape), definite = definiteReferences(shape);
	for (const key of references(template)) if (!supplied.has(key) && !outer.includes(key) && ![Location, Origin].includes(key)) throw new TypeError("unknown template slot");
	validateTemplateReferences(template, new Set([...supplied].filter(key => !definite.has(key) && !outer.includes(key))));
	return createStage(function* (env, value) {
		const occurrence = isOccurrence(value) ? value : { value, location: "" };
		try {
			const captures = capture(occurrence.value, shape);
			if (occurrence.captures) mergeCaptures(captures, occurrence.captures);
			captures[captureKey(Location)] = occurrence.location;
			if (occurrence.origin) captures[captureKey(Origin)] = occurrence.origin;
			const [result, missing] = expand(template, captures);
			for (const loss of captureState(captures).losses) yield new Report(Ledger.Loss, { ...loss, source: occurrence.location });
			for (const hole of missing) {
				if (holes === Policy.Abort) throw new Gap(hole.reason, { path: hole.path, location: occurrence.location });
				yield new Report(Ledger.Gap, { target: reportTarget(env), reason: hole.reason, path: hole.path, source: occurrence.location });
			}
			return result;
		} catch (error) { if (error instanceof Failure && error.location === undefined) error.location = occurrence.location; throw error; }
	}, "project", { input: shape, output: outputShape(template) });
}

/** Process items sequentially, applying `policy` independently. */
export function each(source, policy = Policy.Abort, options = {}) {
	validateOptions(options);
	source = asStage(source);
	return createStage(function* (env, values) {
		const result = [];
		for (const value of values) {
			const output = yield* applyPolicy(env, source, [value], policy, options, true);
			if (output !== ABSENT) result.push(output);
		}
		return result;
	}, "each");
}

/** Process independent items in bounded strands, preserving input order. */
export function parallel(source, policy = Policy.Abort, options = {}) {
	validateOptions(options);
	source = asStage(source);
	const item = createStage(function* (env, value) { return yield* applyPolicy(env, source, [value], policy, options, true); });
	return createStage(function* (env, values) {
		const result = yield new Parallel(item, values);
		return result.filter(value => value !== ABSENT);
	}, "parallel");
}

/** Route matching values to `source` without capturing. */
export function when(shape, source) {
	shape = compileShape(shape);
	validateShape(shape);
	if (references(shape).size) throw new TypeError("routing shapes cannot capture");
	return { shape, stage: asStage(source) };
}
/** Declare a final catch-all branch. */
export function otherwise(source) { return { shape: null, stage: asStage(source) }; }
/** Run the first matching branch. */
export function choose(...branches) {
	if (!branches.length || branches.slice(0, -1).some(branch => branch.shape === null)) throw new TypeError("otherwise must be last");
	return createStage(function* (env, value) {
		for (const branch of branches) if (branch.shape === null || !errors(value, branch.shape).length) return yield* branch.stage(env, value);
		throw new Failure(Failure.Shape, Issue.Mismatch);
	}, "choose");
}
/** Apply `policy` to recoverable failures from `source`. */
export function recover(source, policy, options = {}) {
	validateOptions(options);
	if (policy === Policy.Drop) throw new TypeError("Drop requires each or parallel");
	source = asStage(source);
	return createStage(function* (env, ...inputs) { return yield* applyPolicy(env, source, inputs, policy, options); }, "recover", { input: STAGES.get(source).input, output: STAGES.get(source).output });
}
/** Return `source`'s value together with the ledger entries it reported. */
export function collect(source) {
	source = asStage(source);
	return createStage(function* (env, ...inputs) {
		const before = yield new LedgerRead();
		const value = yield* source(env, ...inputs);
		const after = yield new LedgerRead();
		// Ledger entries are deduplicated; compare values rather than snapshot identity.
		return { value, ledger: after.filter(entry => !before.some(previous => jsonEqual(previous, entry))) };
	}, "collect", { input: STAGES.get(source).input });
}
/** Run `source` on a detached copy and pass the input through. */
export function tap(source) {
	source = asStage(source);
	return createStage(function* (env, value) { yield* source(env, clone(value)); return value; }, "tap");
}
/** Create a run-local accumulator and fold items sequentially. */
export function fold(create, source) {
	source = asStage(source);
	return createStage(function* (env, values) {
		let accumulator = create(env);
		for (const value of values) accumulator = yield* source(env, accumulator, value);
		return accumulator;
	}, "fold");
}
/** Partition values by the first failing `[rule, reason]`. */
export function admit(...rules) {
	return createStage(function* (env, values) {
		const admitted = [], rejected = [];
		for (const value of values) {
			const failed = rules.find(([rule]) => !(typeof rule === "function" ? rule(value) : !errors(value, rule).length));
			if (!failed) admitted.push(value);
			else {
				rejected.push({ value, reason: memberName(failed[1]) });
				yield new Report(Ledger.Gap, { target: reportTarget(env), reason: failed[1] });
			}
		}
		return { Admitted: admitted, Rejected: rejected };
	}, "admit");
}
/** Fail if `measure(value)` exceeds the policy budget at `key`. */
export function bound(key, measure) {
	return createStage(function* (env, value) {
		if (measure(value) > env.policy[key]) throw new Failure(Failure.Budget, Issue.Constraint);
		return yield value;
	}, "bound");
}
/** Retry transient effect failures without multiplying runtime-default attempts. */
export function retry(source, { attempts, delay } = {}) {
	source = asStage(source);
	return createStage(function* (env, ...inputs) {
		const strand = ENV_STRANDS.get(env), limit = attempts ?? env.policy.retry.attempts, wait = delay ?? env.policy.retry.delay ?? DEFAULT_DELAY;
		if (!Number.isInteger(limit) || limit < 1 || limit > env.policy.maxAttempts) throw new Failure(Failure.Budget, Issue.Constraint);
		// Nested retries restore their caller's attempt and suppress default effect retries.
		const previous = [strand.retryDepth, strand.attempt];
		strand.retryDepth++;
		try {
			for (let attempt = 1; attempt <= limit; attempt++) {
				strand.attempt = attempt;
				try { return yield* source(env, ...inputs); }
				catch (failure) {
					if (!retryable(failure)) throw failure;
					if (attempt === limit) { failure.exhausted = true; throw failure; }
					yield env.DelayWait(wait(attempt));
				}
			}
		} finally { [strand.retryDepth, strand.attempt] = previous; }
	}, "retry", { input: STAGES.get(source).input, output: STAGES.get(source).output });
}
/** Admit caller data as a request under the host's permitted `format`. */
export function validate(format) {
	if (!(format instanceof RequestFormat)) throw new TypeError("expected request format");
	return createStage(function* (env, value) {
		const [definition, options] = requestOptions(env, format);
		admitData(value, definition);
		if (!value || typeof value !== "object" || Array.isArray(value)) throw new Failure(Failure.Contract, Issue.Mismatch, { path: [] });
		if (!Object.hasOwn(value, "version")) throw new Failure(Failure.Contract, Issue.Missing, { path: ["version"] });
		if (!Number.isInteger(value.version) || !options.versions.includes(value.version)) throw new Failure(Failure.Contract, Profile.Forbidden, { path: ["version"] });
		const result = format.validate(options, clone(value), clone(env.vocabulary[format.name] ?? {}));
		validateData(result);
		return yield result;
	}, "validate");
}
/** Compile a validated request into a data-only plan. */
export function plan(format) {
	if (!(format instanceof RequestFormat)) throw new TypeError("expected request format");
	return createStage(function* (env, request) {
		const [, options] = requestOptions(env, format), compiled = format.compile(options, clone(request), clone(env.vocabulary[format.name] ?? {}));
		if (env.policy.allow && compiled.allow.some(item => !env.policy.allow.includes(item))) throw new Failure(Failure.Contract, Profile.Forbidden);
		const gaps = compiled.gaps.map(gap => {
			if (!format.gaps.includes(gap.code)) throw new Failure(Failure.Defect, Issue.Unexpected);
			return { code: memberName(gap.code), path: [...gap.path] };
		});
		const result = { request: clone(request), steps: compiled.steps, allow: compiled.allow, budgets: Object.fromEntries(Object.entries(env.policy).filter(([key, value]) => key.startsWith("max") && Number.isInteger(value))), gaps };
		validateData(result);
		return yield result;
	}, "plan");
}
/** Run `cleanup` after success or failure, but never cancellation. */
export function always(source, cleanup) {
	source = asStage(source);
	cleanup = asStage(cleanup);
	return createStage(function* (env, ...inputs) {
		let failure, value, state;
		try { value = yield* source(env, ...inputs); state = { value }; }
		catch (error) {
			if (!(error instanceof Failure) || error.kind === Failure.Cancel) throw error;
			failure = error;
			state = { failure: failureData(error) };
		}
		state.ledger = yield new LedgerRead();
		try { yield* cleanup(env, state); }
		catch (error) { if (!failure) throw error; failure.cleanup = error; }
		if (failure) throw failure;
		return value;
	}, "always", { input: STAGES.get(source).input, output: STAGES.get(source).output });
}

// EOF
