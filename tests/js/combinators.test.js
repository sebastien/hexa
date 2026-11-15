/** Module: combinators.test — Composition, naming, policies and contracts. */

import { expect, test } from "bun:test";

import { Failure, Gap, Int, Issue, Text, capture, codes, decode, derive, either, encode, optional, slots, tolerant, transform } from "hexa/shapes.js";
import * as combinators from "hexa/combinators.js";
import { Policy, admit, always, bound, choose, collect, each, fold, guard, lift, otherwise, pipe, project, recover, stage, stages, tap, when } from "hexa/combinators.js";
import { Checks, Ledger, Runtime, report, run } from "hexa/runtime.js";

const Reason = codes("JsCombinator", "Missing", "Denied");
const Fields = slots("JsCombinatorFields", { Id: Int, Name: Text });

test("policy implementation records are not public exports", () => {
	expect(Object.hasOwn(combinators, "PolicyValue")).toBe(false);
	expect(() => codes("Policy", "Shadow")).toThrow("Runtime.NameRedeclared");
});

test("stage function names and explicit report targets use canonical identity", async () => {
	const named = stage("JsCanonicalTarget", lift(value => value));
	expect(named.name).toBe("JsCanonicalTarget");
	expect(lift(value => value).name).toBe("lift");
	function* JsGeneratorIdentity() { return null; }
	expect(stage(JsGeneratorIdentity).name).toBe("JsGeneratorIdentity");
	function* Reports() {
		yield report(Ledger.Gap, { target: named, reason: Reason.Missing });
		return null;
	}
	expect((await run({}, collect(Reports))).ledger[0].target).toBe("JsCanonicalTarget");
	function* Missing() { throw new Gap(Reason.Missing); }
	const output = await run({}, collect(recover(Missing, Policy.Substitute(1), { target: named })), "bad");
	expect(output.ledger[0].target).toBe("JsCanonicalTarget");
});

test("fixed guards infer output contracts used by substitute policies", async () => {
	const fixed = guard(Int, Reason.Missing);
	function* Missing() { throw new Gap(Reason.Missing); }
	expect(await run({}, fixed, 1)).toBe(1);
	expect(await run({}, recover(pipe(Missing, fixed), Policy.Substitute(1)), "bad")).toBe(1);
	expect((await failed(run({}, recover(pipe(Missing, fixed), Policy.Substitute("bad")), "bad"))).kind).toBe(Failure.Defect);
	expect(await run({}, recover(pipe(Missing, guard(() => Int, Reason.Missing)), Policy.Substitute("dynamic")), "bad")).toBe("dynamic");
});

async function failed(promise) {
	try { await promise; }
	catch (error) { return error; }
	throw new Error("expected failure");
}

test("stage namespaces resolve forward references and trace canonical names", async () => {
	const Flow = stages(S => ({
		Entry: pipe(S.First, S.Second),
		First: lift(value => value + 1),
		Second: lift(value => value * 2),
	}));
	expect(await run({}, Flow.Entry, 3)).toBe(8);
	expect(() => Flow.Unknown).toThrow("Runtime.MemberUnknown");
	expect(Flow.then).toBeUndefined();
	expect(() => stages(S => ({ JsUnresolved: pipe(S.Missing) }))).toThrow("Runtime.MemberUnknown");
	expect(() => stage("JsRenamed", Flow.First)).toThrow("Runtime.StageRenamed");
	expect(encode(Flow.Entry)).toBe("$Entry");
	expect(decode("$Entry")).toBe(Flow.Entry);
});

test("handwritten stage methods delegate through the resolved namespace", async () => {
	const Flow = stages(S => ({
		*JsDelegate(env, value) { return yield* S.JsIncrement(env, value); },
		*JsIncrement(env, value) { return value + 1; },
	}));
	expect(await run({}, Flow.JsDelegate, 1)).toBe(2);
	function* Different() { return null; }
	expect(() => stages(() => ({ JsNotDifferent: Different }))).toThrow("Runtime.StageRenamed");
});

test("drop, abort, substitute and fallback apply only to recoverable failures", async () => {
	function* Item(env, value) { if (value < 0) throw new Gap(Reason.Missing); return value * 2; }
	expect(await run({}, collect(each(Item, Policy.Drop)), [1, -1, 2])).toEqual({
		value: [2, 4], ledger: [{ kind: "Gap", target: "each", reason: "JsCombinator.Missing" }],
	});
	expect(await run({}, each(Item, Policy.Substitute(0)), [1, -1])).toEqual([2, 0]);
	expect(await run({}, each(Item, Policy.Fallback(lift(() => 9))), [-1])).toEqual([9]);
	const failure = await failed(run({}, each(Item), [-1]));
	expect(failure.fatal).toBe(true);
	expect(failure.ledger[0].fatal).toBe(true);
	expect(() => recover(Item, Policy.Drop)).toThrow(TypeError);
	for (const kind of [Failure.Contract, Failure.Budget, Failure.Defect, Failure.Cancel]) {
		function* Fatal() { throw new Failure(kind, Reason.Denied); }
		expect((await failed(run({}, recover(Fatal, Policy.Substitute(1), { on: [kind] })))).kind).toBe(kind);
	}
});

test("effect recovery requires explicit inclusion and preserves substituted data", async () => {
	function* Read(env) { return yield env.Read(); }
	const value = { fallback: [] }, policy = Policy.Substitute(value);
	value.fallback.push("changed");
	const result = await run({ Read() { throw new Error("bad"); } }, recover(Read, policy, { on: [Failure.Effect] }));
	expect(result).toEqual({ fallback: [] });
	result.fallback.push(1);
	expect(await run({ Read() { throw new Error("bad"); } }, recover(Read, policy, { on: [Failure.Effect] }))).toEqual({ fallback: [] });
	expect(encode(decode(encode(policy)))).toEqual(encode(policy));
	expect(encode(Policy)).toEqual({ Abort: "$Policy.Abort", Drop: "$Policy.Drop", Substitute: "$Policy.Substitute", Fallback: "$Policy.Fallback" });
});

test("template holes stay local or activate the item policy when strict", async () => {
	const shape = { id: Fields.Id, name: tolerant(Fields.Name) }, template = { Id: Fields.Id, Name: optional(Fields.Name) };
	const value = [{ id: 1, name: 3 }];
	const output = await run({}, collect(each(project(shape, template), Policy.Drop)), value);
	expect(output.value).toEqual([{ Id: 1 }]);
	expect(output.ledger[0]).toMatchObject({ reason: "Issue.Mismatch", path: ["Name"], source: "" });
	expect(await run({}, each(project(shape, template, { holes: Policy.Abort }), Policy.Drop), value)).toEqual([]);
	expect(() => project({ id: Fields.Id }, { Name: Fields.Name })).toThrow(TypeError);
	expect(() => project(either({ name: Fields.Name }, { id: Fields.Id }), Fields.Name)).toThrow(TypeError);
});

test("contracts check entry inputs and classify bad outputs as defects", async () => {
	const checked = stage("JsCheckedStage", { input: [Int], output: Text }, lift(value => String(value)));
	expect(await run({}, checked, 1)).toBe("1");
	expect((await failed(run({}, checked, "x"))).kind).toBe(Failure.Contract);
	expect((await failed(run({ policy: { contracts: Checks.Off } }, checked, "x"))).kind).toBe(Failure.Contract);
	const bad = stage("JsBadOutput", { input: [Int], output: Text }, lift(value => value));
	expect((await failed(run({}, bad, 1))).kind).toBe(Failure.Defect);
	expect(await run({ policy: { contracts: Checks.Off } }, bad, 1)).toBe(1);
});

test("routing, admission and budget checks compose", async () => {
	const routing = choose(when(Int, lift(value => value + 1)), otherwise(lift(() => "other")));
	expect(await run({}, routing, 2)).toBe(3);
	expect(await run({}, routing, "x")).toBe("other");
	expect(() => when(Fields.Id, routing)).toThrow(TypeError);
	expect(() => choose(otherwise(routing), when(Int, routing))).toThrow(TypeError);
	const result = await run({}, collect(admit([value => value > 0, Reason.Denied])), [1, -1]);
	expect(result.value).toEqual({ Admitted: [1], Rejected: [{ value: -1, reason: "JsCombinator.Denied" }] });
	expect((await failed(run({ policy: { maxGaps: 1 } }, bound("maxGaps", value => value.length), [1, 2]))).kind).toBe(Failure.Budget);
});

test("fold accumulators are run-local and tap works on detached data", async () => {
	function* Append(env, accumulator, value) { accumulator.push(value); return accumulator; }
	const source = fold(() => [], Append);
	expect(await run({}, source, [1, 2])).toEqual([1, 2]);
	expect(await run({}, source, [3])).toEqual([3]);
	const input = { values: [] };
	expect(await run({}, tap(lift(value => { value.values.push(1); return null; })), input)).toBe(input);
	expect(input).toEqual({ values: [] });
});

test("always runs cleanup on success or failure and preserves primary failures", async () => {
	let state;
	function* Cleanup(env, value) { state = value; return null; }
	expect(await run({}, always(lift(() => 7), Cleanup))).toBe(7);
	expect(state).toEqual({ value: 7, ledger: [] });
	function* Primary() { throw new Gap(Reason.Missing); }
	function* Secondary() { throw new Gap(Reason.Denied); }
	const failure = await failed(run({}, always(Primary, Secondary)));
	expect(failure.code).toBe(Reason.Missing);
	expect(failure.cleanup.code).toBe(Reason.Denied);
});

test("ledger budgets count distinct identities", async () => {
	function* Missing() { throw new Gap(Reason.Missing); }
	const result = await run({ policy: { maxGaps: 1 } }, collect(each(Missing, Policy.Drop)), [1, 2]);
	expect(result.ledger).toHaveLength(1);
	const output = await failed(run({ policy: { maxGaps: 0 } }, each(Missing, Policy.Drop), [1]));
	expect(output.kind).toBe(Failure.Budget);
});

// EOF
