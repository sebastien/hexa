/** Module: hexa.runtime — Drive inert effects with structured strands and replay. */

import { ABSENT, DEFAULT_DELAY, LIMITS, Checks, Coverage, Failure, Issue, Ledger, Runtime, clone, entityName, failureData, failureFromData, freeze, isMember, isPlain, isThenable, jsonEqual, memberName, normalise, pointer, retryable, unescapeSegment, validateData } from "./core.js";
import { Effect, Fork, Join, LedgerRead, Parallel, Race, Report } from "./instructions.js";
import { ENV_STRANDS, asStage } from "./stage.js";
import { typeMatches } from "./shape.js";
import { ProfileDefinition } from "./profile.js";

export { report, ledger, join, race, fork } from "./instructions.js";
export { Ledger, Coverage, Checks, Runtime } from "./core.js";

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

const DEFAULT_POLICY = { contracts: Checks.Boundary, maxGaps: 1000, maxConcurrency: 4, maxAttempts: 3, ...LIMITS, retry: { default: false, attempts: 1 } };

class Handle {
	constructor(owner, strand, task) {
		Object.assign(this, { owner, strand, task, joined: false });
	}
}

/** FIFO permits shared by host operations or parallel stage entry points. */
class Gate {
	constructor(limit) {
		this.available = limit;
		this.waiters = [];
	}

	async enter(signal) {
		checkSignal(signal);
		if (this.available > 0) { this.available--; return; }
		await new Promise((resolve, reject) => {
			const waiter = { resolve: () => { signal.removeEventListener("abort", abort); resolve(); } };
			const abort = () => {
				this.waiters.splice(this.waiters.indexOf(waiter), 1);
				reject(cancelFailure());
			};
			signal.addEventListener("abort", abort, { once: true });
			this.waiters.push(waiter);
		});
	}

	leave() {
		if (this.waiters.length) this.waiters.shift().resolve();
		else this.available++;
	}
}

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

function cancelFailure() { return new Failure(Failure.Cancel, Issue.Mismatch); }
function checkSignal(signal) { if (signal.aborted) throw cancelFailure(); }

function environmentSnapshot(input) {
	if (!isPlain(input) || !isPlain(input.policy ?? {}) || !isPlain(input.vocabulary ?? {})) throw new Failure(Failure.Contract, Issue.Mismatch);
	validateData(input.root ?? {});
	validateData(input.scope ?? {});
	const root = clone(input.root ?? {}), scope = clone(input.scope ?? {});
	const policy = { ...DEFAULT_POLICY, ...input.policy, retry: { ...DEFAULT_POLICY.retry, ...input.policy?.retry } };
	for (const key of ["maxGaps", "maxConcurrency", "maxAttempts", "maxDepth", "maxNodes", "maxBytes"]) {
		if (!Number.isSafeInteger(policy[key]) || policy[key] < (key === "maxGaps" ? 0 : 1)) throw new Failure(Failure.Contract, Issue.Constraint);
	}
	if (![Checks.Boundary, Checks.Off].includes(policy.contracts)) throw new Failure(Failure.Contract, Runtime.MemberUnknown);
	if (!Number.isInteger(policy.retry.attempts) || policy.retry.attempts < 1 || policy.retry.attempts > policy.maxAttempts || typeof policy.retry.default !== "boolean" || policy.retry.delay !== undefined && typeof policy.retry.delay !== "function") throw new Failure(Failure.Contract, Issue.Constraint);
	if (policy.allow !== undefined && (!Array.isArray(policy.allow) || policy.allow.some(value => typeof value !== "string"))) throw new Failure(Failure.Contract, Issue.Mismatch);
	if (policy.profile !== undefined && !(policy.profile instanceof ProfileDefinition)) throw new Failure(Failure.Contract, Issue.Mismatch);
	return { root: freeze(root), scope: freeze(scope), policy: freeze(clone(policy)), vocabulary: freeze(clone(input.vocabulary ?? {})) };
}

function standIn(snapshot, strand) {
	const env = new Proxy(Object.freeze({ ...snapshot }), {
		get(target, key) {
			if (Object.hasOwn(target, key)) return target[key];
			if (typeof key === "string" && /^[A-Z]/.test(key)) return (...args) => new Effect(key, args);
			return undefined;
		},
	});
	if (strand) ENV_STRANDS.set(env, strand);
	return env;
}

function entryIdentity(entry) {
	function sorted(value) {
		if (Array.isArray(value)) return value.map(sorted);
		if (isPlain(value)) return Object.fromEntries(Object.keys(value).sort().filter(key => key !== "fatal").map(key => [key, sorted(value[key])]));
		return value;
	}
	return JSON.stringify(sorted(entry));
}

function mergeEntries(target, entries) {
	for (const entry of entries) {
		const existing = target.find(item => entryIdentity(item) === entryIdentity(entry));
		if (!existing) target.push(clone(entry));
		else if (entry.fatal) existing.fatal = true;
	}
}

function replayIndex(replay) {
	if (replay === undefined) return null;
	validateData(replay);
	if (!Array.isArray(replay)) throw new Failure(Failure.Contract, Runtime.ReplayMismatch);
	const result = new Map();
	for (const entry of replay) {
		let key;
		if (!isPlain(entry) || typeof entry.strand !== "string") throw new Failure(Failure.Contract, Runtime.ReplayMismatch);
		if (Object.hasOwn(entry, "instruction")) {
			const keys = entry.keys;
			if (entry.instruction !== "race" || !Number.isSafeInteger(entry.ordinal) || entry.ordinal < 0 || !Array.isArray(keys) || !keys.length || new Set(keys).size !== keys.length ||
				!(keys.every(key => typeof key === "string" && !key.startsWith("@")) || keys.every((key, index) => key === index)) ||
				Object.hasOwn(entry, "winner") === Object.hasOwn(entry, "cancelled") ||
				Object.hasOwn(entry, "winner") && !keys.includes(entry.winner) ||
				Object.hasOwn(entry, "cancelled") && entry.cancelled !== true ||
				Object.keys(entry).some(key => !["index", "instruction", "strand", "ordinal", "keys", "winner", "cancelled"].includes(key))) throw new Failure(Failure.Contract, Runtime.ReplayMismatch);
			key = JSON.stringify([entry.strand, "race", entry.ordinal]);
		} else {
			if (!Number.isSafeInteger(entry.seq) || entry.seq < 0 || ["result", "failure", "cancelled"].filter(key => Object.hasOwn(entry, key)).length !== 1) throw new Failure(Failure.Contract, Runtime.ReplayMismatch);
			key = JSON.stringify([entry.strand, "effect", entry.seq]);
		}
		if (result.has(key)) throw new Failure(Failure.Contract, Runtime.ReplayMismatch);
		result.set(key, clone(entry));
	}
	return result;
}

function delayWait(milliseconds, signal) {
	return new Promise((resolve, reject) => {
		checkSignal(signal);
		const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(null); }, milliseconds);
		const abort = () => { clearTimeout(timer); reject(cancelFailure()); };
		signal.addEventListener("abort", abort, { once: true });
	});
}

// -----------------------------------------------------------------------------
//
// OPERATIONS
//
// -----------------------------------------------------------------------------

class Run {
	constructor(environment) {
		this.snapshot = environmentSnapshot(environment);
		this.policy = this.snapshot.policy;
		this.operations = new Map(Object.entries(environment).filter(([key, value]) => /^[A-Z]/.test(key) && typeof value === "function"));
		if (environment.record != null && !["boolean", "function"].includes(typeof environment.record)) throw new TypeError("record must be a boolean or a function");
		this.record = Boolean(environment.record);
		this.sink = typeof environment.record === "function" ? environment.record : null;
		this.sinkFailed = false;
		this.sinkError = undefined;
		this.replay = replayIndex(environment.replay);
		this.usedReplay = new Set();
		this.journal = [];
		this.origins = new WeakMap();
		this.counters = new Map();
		this.active = new Set();
		this.ledgerKeys = new Set();
		this.completions = 0;
		this.gate = new Gate(this.policy.maxConcurrency);
	}

	next(strand, domain) {
		const key = JSON.stringify([strand.id, domain]), index = this.counters.get(key) ?? 0;
		this.counters.set(key, index + 1);
		return index;
	}

	appendJournal(entry) {
		if (!this.record) return;
		const item = { ...clone(entry), index: this.journal.length };
		this.journal.push(item);
		if (!this.sink) return;
		// A failing sink must not replace the outcome in flight: it is dropped and fails the run at its end.
		try { this.sink(clone(item)); }
		catch (error) { this.sink = null; this.sinkFailed = true; this.sinkError = error; }
	}

	validate(value) { validateData(value, this.policy); }
}

class Strand {
	constructor(run, parent = null, id = "") {
		Object.assign(this, { run, parent, id });
		this.controller = new AbortController();
		this.entries = [];
		this.handles = [];
		this.stack = [...(parent?.stack ?? [])];
		this.retryDepth = parent?.retryDepth ?? 0;
		this.attempt = parent?.attempt ?? 1;
		this.env = standIn(run.snapshot, this);
		this.closing = false;
		this.done = false;
	}

	report(instruction) {
		if (![Ledger.Gap, Ledger.Loss].includes(instruction.kind) || !isMember(instruction.reason)) throw new TypeError("invalid ledger classification");
		const entry = { kind: memberName(instruction.kind).split(".").at(-1), target: entityName(instruction.target), reason: memberName(instruction.reason) };
		for (const key of ["source", "path"]) if (instruction[key] !== ABSENT) entry[key] = clone(instruction[key]);
		if (instruction.fatal) entry.fatal = true;
		validateData(entry);
		const key = entryIdentity(entry);
		if (!this.run.ledgerKeys.has(key)) {
			if (this.run.ledgerKeys.size >= this.run.policy.maxGaps) throw new Failure(Failure.Budget, Issue.Constraint);
			this.run.ledgerKeys.add(key);
		}
		mergeEntries(this.entries, [entry]);
	}

	mark(value, seq, path = []) {
		if (!Array.isArray(value) && !isPlain(value)) return;
		this.run.origins.set(value, { strand: this.id, seq, pointer: pointer(path) });
		for (const [key, child] of Object.entries(value)) this.mark(child, seq, [...path, Array.isArray(value) ? +key : key]);
	}

	origin(value, location) {
		let origin;
		for (const segment of [...(location ? location.split("/").slice(1) : []), null]) {
			if (value && typeof value === "object" && this.run.origins.has(value)) origin = clone(this.run.origins.get(value));
			if (segment === null) break;
			if (origin) origin.pointer += "/" + segment;
			value = value[unescapeSegment(segment)];
		}
		return origin;
	}

	classify(error) {
		if (this.controller.signal.aborted || error?.name === "AbortError" || error?.name === "TimeoutError") return cancelFailure();
		return error instanceof Failure ? error : new Failure(Failure.Defect, Issue.Mismatch, { cause: error });
	}

	async drive(source, inputs) {
		const iterator = source(this.env, ...inputs);
		let finished = false;
		try {
			let step = iterator.next();
			while (!step.done) {
				checkSignal(this.controller.signal);
				let value, failure;
				try {
					if (isThenable(step.value)) throw new Failure(Failure.Defect, Runtime.NativeAwaitable);
					value = this.perform(step.value);
					if (isThenable(value)) value = await value;
					checkSignal(this.controller.signal);
				} catch (error) {
					failure = this.classify(error);
					if (!failure.trace.length) failure.trace = [...this.stack];
					if (failure.kind === Failure.Cancel) throw failure;
				}
				step = failure ? iterator.throw(failure) : iterator.next(value);
			}
			checkSignal(this.controller.signal);
			if (this.handles.some(handle => !handle.joined)) throw new Failure(Failure.Defect, Runtime.StrandUnjoined);
			finished = true;
			return step.value;
		} catch (error) {
			const failure = this.classify(error);
			if (!failure.trace.length) failure.trace = [...this.stack];
			throw failure;
		} finally {
			if (!finished) this.controller.abort();
			this.closing = true;
			let closeFailure;
			try {
				if (!iterator.return().done) closeFailure = new Failure(Failure.Defect, Runtime.CloseYielded);
			} finally {
				const pending = this.handles.filter(handle => !handle.joined);
				for (const handle of pending) handle.strand.controller.abort();
				await Promise.allSettled(pending.map(handle => handle.task));
			}
			if (closeFailure) throw closeFailure;
		}
	}

	async callOperation(operation, args, attempt) {
		const signal = this.controller.signal;
		await this.run.gate.enter(signal);
		try {
			checkSignal(signal);
			const fn = this.run.operations.get(operation);
			if (fn) return await fn(...args, { signal, attempt, strand: this.id });
			if (operation === "DelayWait") return await delayWait(args[0], signal);
			if (operation === "ProgressPublish") return null;
			throw new Failure(Failure.Effect, Runtime.MemberUnknown);
		} catch (error) {
			if (error instanceof Failure || signal.aborted || ["AbortError", "TimeoutError"].includes(error?.name)) throw this.classify(error);
			const classifier = this.run.operations.get("FailureClassify");
			const classification = classifier ? await classifier(error) : { code: Issue.Mismatch, transient: false };
			const failure = new Failure(Failure.Effect, classification.code, { cause: error });
			failure.transient = Boolean(classification.transient);
			throw failure;
		} finally { this.run.gate.leave(); }
	}

	async effectOnce(effect, attempt) {
		const args = normalise(clone(effect.arguments)), seq = this.run.next(this, "effect");
		this.run.validate(args);
		const entry = { strand: this.id, seq, operation: effect.operation, arguments: clone(args), attempt };
		try {
			let result;
			if (this.run.replay) {
				const key = JSON.stringify([this.id, "effect", seq]), recorded = this.run.replay.get(key);
				if (!recorded || recorded.operation !== effect.operation || !jsonEqual(recorded.arguments, args) || (recorded.attempt ?? 1) !== attempt) throw new Failure(Failure.Defect, Runtime.ReplayMismatch);
				this.run.usedReplay.add(key);
				if (recorded.cancelled) throw cancelFailure();
				if (recorded.failure) throw failureFromData(recorded.failure);
				result = clone(recorded.result);
			} else result = await this.callOperation(effect.operation, args, attempt);
			checkSignal(this.controller.signal);
			try { this.run.validate(result); }
			catch (error) {
				if (error instanceof Failure && error.kind === Failure.Contract) throw new Failure(Failure.Defect, error.code);
				throw error;
			}
			result = normalise(result);
			entry.result = clone(result);
			this.mark(result, seq);
			return result;
		} catch (error) {
			const failure = this.classify(error);
			if (failure.kind === Failure.Cancel) entry.cancelled = true;
			else entry.failure = failureData(failure, { detail: true });
			throw failure;
		} finally { this.run.appendJournal(entry); }
	}

	async effect(value) {
		const defaults = this.run.policy.retry;
		if (!defaults.default || this.retryDepth || value.operation === "DelayWait") return this.effectOnce(value, this.retryDepth ? this.attempt : 1);
		for (let attempt = 1; attempt <= defaults.attempts; attempt++) {
			try { return await this.effectOnce(value, attempt); }
			catch (error) {
				if (!retryable(error)) throw error;
				if (attempt === defaults.attempts) { error.exhausted = true; throw error; }
				await this.effectOnce(new Effect("DelayWait", [(defaults.delay ?? DEFAULT_DELAY)(attempt)]), attempt);
			}
		}
	}

	child(id) {
		if (this.run.active.has(id)) throw new Failure(Failure.Defect, Runtime.StrandConflict);
		this.run.active.add(id);
		return new Strand(this.run, this, id);
	}

	async childRun(child, item, inputs, gate) {
		const abort = () => child.controller.abort();
		this.controller.signal.addEventListener("abort", abort, { once: true });
		if (this.controller.signal.aborted) abort();
		let entered = false, outcome;
		try {
			if (gate) { await gate.enter(child.controller.signal); entered = true; }
			checkSignal(child.controller.signal);
			const value = inputs === undefined ? await child.perform(item) : await child.drive(item, inputs);
			outcome = { value };
		} catch (error) { outcome = { error: error instanceof Failure ? error : child.classify(error) }; }
		finally {
			if (entered) gate.leave();
			this.controller.signal.removeEventListener("abort", abort);
			this.run.active.delete(child.id);
			child.done = true;
		}
		return { ...outcome, order: this.run.completions++ };
	}

	startFork(stage, inputs, gate) {
		const child = this.child(`${this.id}/@${this.run.next(this, "fork")}`);
		const handle = new Handle(this, child, this.childRun(child, stage, inputs, gate));
		this.handles.push(handle);
		return handle;
	}

	async runItems(items, racing = false) {
		if (!(Array.isArray(items) || isPlain(items)) || !Array.isArray(items) && Object.keys(items).some(key => key.startsWith("@"))) throw new TypeError("join/race requires list or struct");
		const pairs = Object.entries(items).map(([key, value]) => [Array.isArray(items) ? +key : key, value]);
		if (!pairs.length) {
			if (racing) throw new Failure(Failure.Contract, Issue.Missing);
			return Array.isArray(items) ? [] : {};
		}
		const handles = pairs.map(([, value]) => value).filter(value => value instanceof Handle);
		if (new Set(handles).size !== handles.length || handles.some(handle => handle.owner !== this || handle.joined)) throw new Failure(Failure.Defect, Runtime.StrandUnjoined);
		const keys = pairs.map(([key]) => key);
		let decision, recorded;
		if (racing) {
			decision = { instruction: "race", strand: this.id, ordinal: this.run.next(this, "race"), keys };
			if (this.run.replay) {
				const key = JSON.stringify([this.id, "race", decision.ordinal]);
				recorded = this.run.replay.get(key);
				if (!recorded || !jsonEqual(recorded.keys, keys)) throw new Failure(Failure.Defect, Runtime.ReplayMismatch);
				this.run.usedReplay.add(key);
			}
		}
		const strands = [], tasks = [];
		for (const [key, item] of pairs) {
			if (item instanceof Handle) { strands.push(item.strand); tasks.push(item.task); }
			else {
				const child = this.child(this.id + pointer([key]));
				strands.push(child);
				tasks.push(this.childRun(child, item));
			}
		}
		let winner, firstError;
		const outcomes = new Array(tasks.length);
		try {
			if (this.run.replay) {
				const all = await Promise.all(tasks);
				all.forEach((outcome, index) => { outcomes[index] = outcome; });
				if (racing) {
					if (Object.hasOwn(recorded, "winner")) {
						winner = keys.indexOf(recorded.winner);
						decision.winner = keys[winner];
						firstError = all[winner].error;
					} else { decision.cancelled = true; firstError = cancelFailure(); }
					this.run.appendJournal(decision);
				} else firstError = [...all].sort((a, b) => a.order - b.order).find(outcome => outcome.error)?.error;
			} else {
				const completed = new Map();
				const pending = new Map(tasks.map((task, index) => [index, task.then(outcome => { completed.set(index, outcome); return outcome; })]));
				while (pending.size) {
					await Promise.race(pending.values());
					const [index, outcome] = [...completed].filter(([index]) => pending.has(index)).sort((a, b) => a[1].order - b[1].order)[0];
					pending.delete(index);
					outcomes[index] = outcome;
					if (this.controller.signal.aborted) {
						firstError = cancelFailure();
						if (racing) { decision.cancelled = true; this.run.appendJournal(decision); }
						break;
					}
					if (racing) {
						winner = index;
						decision.winner = keys[index];
						this.run.appendJournal(decision);
					}
					if (outcome.error) firstError = outcome.error;
					if (racing || firstError) break;
				}
			}
		} finally {
			// Replay drains losers: aborting early could erase recorded nested work.
			if (!this.run.replay) for (let index = 0; index < tasks.length; index++) if (!strands[index].done) strands[index].controller.abort();
			const all = await Promise.all(tasks);
			all.forEach((outcome, index) => { outcomes[index] = outcome; });
			for (const handle of handles) handle.joined = true;
			for (const child of strands) mergeEntries(this.entries, child.entries);
		}
		for (const outcome of outcomes) {
			if (outcome.error?.code === Runtime.ReplayMismatch) firstError = outcome.error;
			else if (outcome.error?.code === Runtime.CloseYielded) {
				if (firstError && firstError !== outcome.error) firstError.cleanup = outcome.error;
				else firstError = outcome.error;
			}
		}
		if (firstError) throw firstError;
		if (racing) return Array.isArray(items) ? outcomes[winner].value : { [keys[winner]]: outcomes[winner].value };
		return Array.isArray(items) ? outcomes.map(outcome => outcome.value) : Object.fromEntries(keys.map((key, index) => [key, outcomes[index].value]));
	}

	perform(value) {
		if (value instanceof Effect) return this.effect(value);
		if (value instanceof Report) { this.report(value); return null; }
		if (value instanceof LedgerRead) return clone(this.entries);
		if (value instanceof Race) return this.runItems(value.items, true);
		if (value instanceof Join) return this.runItems(value.items);
		if (value instanceof Fork) return this.startFork(value.stage, value.inputs);
		if (value instanceof Parallel) {
			const gate = new Gate(this.run.policy.maxConcurrency);
			return this.runItems(value.values.map(item => this.startFork(value.stage, [item], gate)));
		}
		return value;
	}
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/** Snapshot authority into an inert stage-facing environment. */
export function inert(environment) {
	return standIn(environmentSnapshot(environment));
}

/** Encode every interpolation as one validated path segment. */
export function p(strings, ...values) {
	if (!Array.isArray(strings) || !Object.hasOwn(strings, "raw")) throw new TypeError("p requires a tagged template");
	return strings.reduce((result, literal, index) => {
		if (!index) return literal;
		let value = values[index - 1];
		if (!(typeMatches(value, "Text") || typeMatches(value, "Int")) || ["", ".", ".."].includes(value)) throw new Failure(Failure.Contract, Runtime.PathSegmentInvalid);
		value = String(value);
		try { return result + encodeURIComponent(value).replace(/[!'()*]/g, character => "%" + character.charCodeAt(0).toString(16).toUpperCase()) + literal; }
		catch (cause) { throw new Failure(Failure.Contract, Runtime.PathSegmentInvalid, { cause }); }
	}, "");
}

/** Run a generator stage against snapshotted host authority. */
export async function run(environment, stage, ...inputs) {
	const state = new Run(environment), strand = new Strand(state);
	for (const value of inputs) state.validate(value);
	const signal = environment.signal;
	if (signal !== undefined && !(signal instanceof AbortSignal)) throw new TypeError("signal must be AbortSignal");
	const abort = () => strand.controller.abort();
	signal?.addEventListener("abort", abort, { once: true });
	if (signal?.aborted) abort();
	try {
		checkSignal(strand.controller.signal);
		const value = await strand.drive(asStage(stage), inputs);
		if (state.replay && state.usedReplay.size !== state.replay.size) throw new Failure(Failure.Defect, Runtime.ReplayMismatch);
		if (state.sinkFailed) throw new Failure(Failure.Defect, Runtime.RecordFailed, { cause: state.sinkError });
		return value;
	} catch (error) {
		const failure = error instanceof Failure ? error : new Failure(Failure.Defect, Issue.Mismatch, { cause: error });
		failure.ledger = clone(strand.entries);
		failure.journal = state.record ? clone(state.journal) : undefined;
		throw failure;
	} finally { signal?.removeEventListener("abort", abort); }
}

// EOF
