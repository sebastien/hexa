/** Module: runtime.test — Structured concurrency, retry, cancellation and replay. */

import { expect, test } from "bun:test";

import { Failure, Gap, Issue, Text, codes, profile } from "hexa/shapes.js";
import { Policy, always, collect, each, lift, parallel, recover, retry } from "hexa/combinators.js";
import { Ledger, Runtime, fork, inert, join, ledger, p, race, report, run } from "hexa/runtime.js";

const Reason = codes("JsRuntime", "Missing");

function deferred() {
	let resolve, reject;
	const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
	return { promise, resolve, reject };
}

async function failed(promise) {
	try { await promise; }
	catch (error) { return error; }
	throw new Error("expected failure");
}

/** Force a terminal failure to expose the successful work's journal. */
function recorded(source) {
	return function* Recorded(env, ...inputs) {
		yield* source(env, ...inputs);
		throw new Gap(Reason.Missing);
	};
}

test("plain yields, including instruction-shaped objects, are values", async () => {
	function* Plain() {
		expect(yield 3).toBe(3);
		const value = { operation: "Read", arguments: [] };
		expect(yield value).toBe(value);
		return value;
	}
	expect(await run({}, Plain)).toEqual({ operation: "Read", arguments: [] });
});

test("plain instructions resume synchronously until an effect", async () => {
	const events = [];
	function* Immediate() { events.push("start"); yield 1; yield ledger(); events.push("end"); return null; }
	const promise = run({}, Immediate);
	events.push("host");
	await promise;
	expect(events).toEqual(["start", "end", "host"]);
});

test("native promises and async stages are rejected", async () => {
	function* Native() { yield Promise.resolve(1); }
	expect((await failed(run({}, Native))).code).toBe(Runtime.NativeAwaitable);
	expect((await failed(run({}, async function Async() {}))).kind).toBe(Failure.Defect);
});

test("ledger snapshots are detached and fatal reports deduplicate", async () => {
	function* Reports() {
		yield report(Ledger.Gap, { target: Reason, reason: Reason.Missing });
		const first = yield ledger(); first[0].reason = "changed";
		yield report(Ledger.Gap, { target: Reason, reason: Reason.Missing, fatal: true });
		return yield ledger();
	}
	expect(await run({}, Reports)).toEqual([{ kind: "Gap", target: "JsRuntime", reason: "JsRuntime.Missing", fatal: true }]);
});

test("effect host context, snapshots and result normalization", async () => {
	const root = { id: 1 };
	const input = { root, Read(value, context) {
		expect(value).toBe(1);
		expect(context.attempt).toBe(1);
		expect(context.strand).toBe("");
		expect(context.signal).toBeInstanceOf(AbortSignal);
		return -0;
	} };
	function* Read(env) { expect(Object.isFrozen(env.root)).toBe(true); return yield env.Read(env.root.id); }
	const output = run(input, Read); root.id = 2;
	expect(Object.is(await output, -0)).toBe(false);
});

test("inert operations never call the host", () => {
	const env = inert({ Read() { throw new Error("called"); } });
	expect(env.Read("x")).toMatchObject({ operation: "Read", arguments: ["x"] });
	expect(Object.isFrozen(env)).toBe(true);
});

test("profile policy accepts definitions and rejects forged markers", async () => {
	const definition = profile("JsRuntimeProfile");
	expect(inert({ policy: { profile: definition } }).policy.profile).toBe(definition);
	for (const value of [{ profileDefinition: true }, {}, null]) {
		expect(() => inert({ policy: { profile: value } })).toThrow("Issue.Mismatch");
		expect((await failed(run({ policy: { profile: value } }, lift(() => null)))).kind).toBe(Failure.Contract);
	}
});

test("effects snapshot and freeze nested arguments", async () => {
	const value = { items: [1] }, effect = inert({}).Publish(value);
	value.items.push(2);
	expect(() => effect.arguments[0].items.push(3)).toThrow();
	expect(() => { effect.arguments[0].items = []; }).toThrow();
	function* Publish() { return yield effect; }
	expect(await run({ Publish: argument => { argument.items.push(4); return argument; } }, Publish)).toEqual({ items: [1, 4] });
	expect(effect.arguments).toEqual([{ items: [1] }]);
});

test("join and race validate original containers before copying", async () => {
	class Items { get value() { throw new Error("must not read"); } }
	for (const make of [join, race]) {
		for (const value of [undefined, null, 1, "text", true, () => {}, new Date(), new Map(), new Items()]) {
			expect(() => make(value)).toThrow("join/race requires list or struct");
		}
		for (const value of [[1], { value: 1 }, Object.assign(Object.create(null), { value: 1 })]) {
			const instruction = make(value);
			expect(instruction.items).not.toBe(value);
			expect(instruction.items).toEqual(value);
		}
	}
	function* Join() { return yield join(Object.assign(Object.create(null), { value: 1 })); }
	function* Race() { return yield race([1]); }
	expect(await run({}, Join)).toEqual({ value: 1 });
	expect(await run({}, Race)).toBe(1);
});

test("repeated joins have per-strand effect sequence numbers", async () => {
	function* Twice(env) { yield join([env.Read(1)]); return yield join([env.Read(2)]); }
	const failure = await failed(run({ record: true, Read: value => value }, recorded(Twice)));
	expect(failure.journal.map(entry => [entry.strand, entry.seq])).toEqual([["/0", 0], ["/0", 1]]);
	expect(await run({ replay: failure.journal }, Twice)).toEqual([2]);
});

test("retry journals delays and replay never sleeps or invokes hosts", async () => {
	let calls = 0;
	function* Read(env) { return yield env.Read(); }
	const source = retry(Read, { attempts: 3, delay: () => 0 });
	const failure = await failed(run({
		record: true,
		Read() { if (++calls < 3) throw new Error("temporary"); return "ok"; },
		FailureClassify: () => ({ code: Reason.Missing, transient: true }),
	}, recorded(source)));
	expect(calls).toBe(3);
	expect(failure.journal.map(entry => entry.operation)).toEqual(["Read", "DelayWait", "Read", "DelayWait", "Read"]);
	expect(failure.journal.map(entry => entry.attempt)).toEqual([1, 1, 2, 2, 3]);
	expect(await run({ replay: failure.journal }, source)).toBe("ok");
});

test("retry exhaustion cannot be recovered", async () => {
	function* Read(env) { return yield env.Read(); }
	const source = recover(retry(Read, { attempts: 2, delay: () => 0 }), Policy.Substitute("fallback"), { on: [Failure.Effect] });
	const failure = await failed(run({ Read() { throw new Error("temporary"); }, FailureClassify: () => ({ code: Reason.Missing, transient: true }) }, source));
	expect(failure.exhausted).toBe(true);
});

test("runtime default retry does not multiply explicit retries", async () => {
	let calls = 0;
	function* Read(env) { return yield env.Read(); }
	const failure = await failed(run({
		policy: { retry: { default: true, attempts: 3, delay: () => 0 } },
		Read() { calls++; throw new Error("temporary"); },
		FailureClassify: () => ({ code: Reason.Missing, transient: true }),
	}, retry(Read, { attempts: 2, delay: () => 0 })));
	expect(failure.exhausted).toBe(true);
	expect(calls).toBe(2);
});

test("parallel bounds stage work and preserves declaration order", async () => {
	let active = 0, maximum = 0;
	function* Item(env, value) { return yield env.Work(value); }
	const result = await run({
		policy: { maxConcurrency: 2 },
		async Work(value) {
			maximum = Math.max(maximum, ++active);
			await new Promise(resolve => setTimeout(resolve, value === 1 ? 5 : 0));
			active--;
			return value * 2;
		},
	}, parallel(Item), [1, 2, 3, 4]);
	expect(result).toEqual([2, 4, 6, 8]);
	expect(maximum).toBe(2);
});

test("fork handles must be joined once by their owning strand", async () => {
	function* Child(env, value) { return yield env.Read(value); }
	function* Parent() { const handle = yield fork(Child, 7); return yield join([handle]); }
	expect(await run({ Read: value => value }, Parent)).toEqual([7]);
	function* Unjoined() { yield fork(Child, 7); return null; }
	expect((await failed(run({ Read: value => value }, Unjoined))).code).toBe(Runtime.StrandUnjoined);
	function* Duplicate() { const handle = yield fork(Child, 7); return yield join([handle, handle]); }
	expect((await failed(run({ Read: value => value }, Duplicate))).code).toBe(Runtime.StrandUnjoined);
});

test("race cancels losers and replays its explicit winner", async () => {
	let cancelled = false;
	function* Race(env) { return yield race({ Slow: env.Slow(), Fast: env.Fast() }); }
	const failure = await failed(run({
		record: true,
		Slow: (context) => new Promise((resolve, reject) => context.signal.addEventListener("abort", () => { cancelled = true; reject(new DOMException("abort", "AbortError")); }, { once: true })),
		Fast: () => "won",
	}, recorded(Race)));
	expect(cancelled).toBe(true);
	expect(failure.journal.find(entry => entry.instruction === "race").winner).toBe("Fast");
	expect(failure.journal.find(entry => entry.operation === "Slow").cancelled).toBe(true);
	expect(await run({ replay: failure.journal }, Race)).toEqual({ Fast: "won" });
});

test("effect-free races still journal decisions and preserve actual handle completion", async () => {
	function* Pure(env, value) { return value; }
	function* Race() {
		const first = yield fork(Pure, "earlier");
		const second = yield fork(Pure, "later");
		return yield race({ Later: second, Earlier: first });
	}
	const failure = await failed(run({ record: true }, recorded(Race)));
	expect(failure.journal).toHaveLength(1);
	expect(failure.journal[0].winner).toBe("Earlier");
	expect(await run({ replay: failure.journal }, Race)).toEqual({ Earlier: "earlier" });
});

test("already completed handles race by completion order, not input order", async () => {
	function* Pure(env, value) { return value; }
	function* Race(env) {
		const first = yield fork(Pure, "earlier");
		yield env.Tick();
		const second = yield fork(Pure, "later");
		yield env.Tick();
		return yield race({ Later: second, Earlier: first });
	}
	expect(await run({ Tick: () => null }, Race)).toEqual({ Earlier: "earlier" });
});

test("externally cancelled races record cancellation rather than a winning child", async () => {
	const started = deferred(), controller = new AbortController();
	function* Race(env) { return yield race([env.Block()]); }
	const pending = run({
		record: true, signal: controller.signal,
		Block: context => {
			started.resolve();
			return new Promise((resolve, reject) => context.signal.addEventListener("abort", () => reject(new DOMException("abort", "AbortError")), { once: true }));
		},
	}, Race);
	await started.promise;
	controller.abort();
	const failure = await failed(pending);
	const decision = failure.journal.find(entry => entry.instruction);
	expect(decision.cancelled).toBe(true);
	expect(Object.hasOwn(decision, "winner")).toBe(false);
	expect((await failed(run({ replay: failure.journal }, Race))).kind).toBe(Failure.Cancel);
});

test("race failure is a completion, not a fallback", async () => {
	function* Race(env) { return yield race([env.Bad(), env.Good()]); }
	const failure = await failed(run({ Bad() { throw new Gap(Reason.Missing); }, Good: () => 1 }, Race));
	expect(failure.code).toBe(Reason.Missing);
});

test("replay refuses legacy or malformed race decisions", async () => {
	function* Race() { return yield race([1, 2]); }
	expect((await failed(run({ replay: [] }, Race))).code).toBe(Runtime.ReplayMismatch);
	for (const decision of [
		{ instruction: "race", strand: "", ordinal: 0, keys: [0, 1], winner: 3 },
		{ instruction: "race", strand: "", ordinal: 0, keys: [0, 0], winner: 0 },
		{ instruction: "race", strand: "", ordinal: 0, keys: [0], winner: 0, cancelled: true },
	]) expect((await failed(run({ replay: [decision] }, Race))).code).toBe(Runtime.ReplayMismatch);
});

test("nested race replay consumes loser work and detects its mismatches", async () => {
	function* Child(env) { return yield join([env.Read(1), env.Read(2)]); }
	function* Nested(env) {
		const handle = yield fork(Child);
		return yield race({ Nested: handle, Direct: env.Read(3) });
	}
	const failure = await failed(run({ record: true, Read: value => value }, recorded(Nested)));
	const replayed = await run({ replay: failure.journal }, Nested);
	const winner = failure.journal.find(entry => entry.instruction === "race").winner;
	expect(replayed).toEqual(winner === "Direct" ? { Direct: 3 } : { Nested: [1, 2] });
	const journal = structuredClone(failure.journal);
	journal.find(entry => entry.operation === "Read").arguments = [99];
	expect((await failed(run({ replay: journal }, Nested))).code).toBe(Runtime.ReplayMismatch);
});

test("race ordinals do not consume effect sequence positions", async () => {
	function* Repeated(env) { yield race([1]); yield race([2]); return yield env.Read(); }
	const failure = await failed(run({ record: true, Read: () => 3 }, recorded(Repeated)));
	expect(failure.journal.filter(entry => entry.instruction).map(entry => entry.ordinal)).toEqual([0, 1]);
	expect(failure.journal.find(entry => entry.operation).seq).toBe(0);
	expect(await run({ replay: failure.journal }, Repeated)).toBe(3);
});

test("sibling failure cancels pending host work and joins cleanup", async () => {
	let cancelled = false;
	function* Together(env) { return yield join([env.Block(), env.Bad()]); }
	const failure = await failed(run({
		Block: context => new Promise((resolve, reject) => context.signal.addEventListener("abort", () => { cancelled = true; reject(new DOMException("abort", "AbortError")); }, { once: true })),
		Bad() { throw new Gap(Reason.Missing); },
	}, Together));
	expect(failure.code).toBe(Reason.Missing);
	expect(cancelled).toBe(true);
});

test("host cancellation closes generators without recovery or always cleanup", async () => {
	const started = deferred(), controller = new AbortController();
	let caught = false, closed = false, cleaned = false;
	function* Block(env) {
		try { yield env.Block(); }
		catch { caught = true; }
		finally { closed = true; }
	}
	function* Cleanup() { cleaned = true; return null; }
	const pending = run({
		signal: controller.signal,
		Block: context => { started.resolve(); return new Promise((resolve, reject) => context.signal.addEventListener("abort", () => reject(new DOMException("abort", "AbortError")), { once: true })); },
	}, always(recover(Block, Policy.Substitute(null), { on: [Failure.Cancel] }), Cleanup));
	await started.promise;
	controller.abort();
	expect((await failed(pending)).kind).toBe(Failure.Cancel);
	expect(closed).toBe(true);
	expect(caught).toBe(false);
	expect(cleaned).toBe(false);
});

test("yield during generator closure is a defect", async () => {
	function* Closing(env) { try { yield env.Block(); } finally { yield 1; } }
	const failure = await failed(run({ Block() { throw new DOMException("timeout", "TimeoutError"); } }, Closing));
	expect(failure.code).toBe(Runtime.CloseYielded);
});

test("timeout host failures classify as cancellation", async () => {
	function* Read(env) { return yield env.Read(); }
	expect((await failed(run({ Read() { throw new DOMException("timeout", "TimeoutError"); } }, Read))).kind).toBe(Failure.Cancel);
});

test("successful runs do not abort completed host contexts", async () => {
	let signal;
	function* Read(env) { return yield env.Read(); }
	expect(await run({ Read: context => { signal = context.signal; return 1; } }, Read)).toBe(1);
	expect(signal.aborted).toBe(false);
});

test("replay detects arguments, missing entries and unconsumed entries", async () => {
	function* Read(env) { return yield env.Read(1); }
	const journal = [{ strand: "", seq: 0, operation: "Read", arguments: [2], result: 1 }];
	expect((await failed(run({ replay: journal }, Read))).code).toBe(Runtime.ReplayMismatch);
	expect((await failed(run({ replay: [] }, Read))).code).toBe(Runtime.ReplayMismatch);
	function* Empty() { return null; }
	expect((await failed(run({ replay: journal }, Empty))).code).toBe(Runtime.ReplayMismatch);
});

test("private failure evidence is excluded from JSON", async () => {
	function* Fail(env) { yield env.Read(); throw new Gap(Reason.Missing); }
	const failure = await failed(run({ record: true, Read: () => ({ secret: "private" }) }, Fail));
	expect(failure.journal[0].result).toEqual({ secret: "private" });
	expect(JSON.stringify(failure)).not.toContain("private");
	expect(Object.keys(failure)).not.toContain("ledger");
	expect(Object.keys(failure)).not.toContain("journal");
});

test("invalid host results are defects while boundary input is a contract failure", async () => {
	function* Read(env) { return yield env.Read(); }
	expect((await failed(run({ Read: () => undefined }, Read))).kind).toBe(Failure.Defect);
	expect((await failed(run({}, lift(value => value), { bad: undefined }))).kind).toBe(Failure.Contract);
	expect((await failed(run({ policy: { maxBytes: 1 }, Read: () => "long" }, Read))).kind).toBe(Failure.Budget);
});

test("paths validate and encode interpolations only", () => {
	expect(p`/clients/${"a/b"}/${42}`).toBe("/clients/a%2Fb/42");
	expect(p`/clients/${-0}`).toBe("/clients/0");
	expect(p`/${"!'()*-._~"}`).toBe("/%21%27%28%29%2A-._~");
	for (const value of ["", ".", "..", undefined, null, true, 1.5, 2 ** 53, "\uD800"]) expect(() => p`/${value}`).toThrow("Runtime.PathSegmentInvalid");
	expect(() => p("/not-a-tag")).toThrow(TypeError);
});

// EOF
