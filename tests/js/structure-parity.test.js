/** Module: structure-parity.test — Shared API, identity and capture regression vectors. */

import { readFileSync } from "node:fs";

import { expect, test } from "bun:test";

import * as Shapes from "hexa/shapes.js";
import * as Combinators from "hexa/combinators.js";
import * as Runtime from "hexa/runtime.js";
import { REGISTRY } from "../../src/js/hexa/core.js";
import { captureState } from "../../src/js/hexa/shape.js";

const fixture = JSON.parse(readFileSync(new URL("../fixtures/structure-parity.json", import.meta.url), "utf8"));
const { Any, Failure, Int, Issue, Text, capture, codec, codes, either, errors, evidence, gather, lossy, pattern, slot, via, where } = Shapes;

test("public module exports match the shared binding contract", () => {
	for (const [name, module] of Object.entries({ shapes: Shapes, combinators: Combinators, runtime: Runtime })) {
		expect(Object.keys(module).sort()).toEqual(fixture.exports[name].toSorted());
	}
});

test("constraint identities include the predicate and reject invalid callbacks", () => {
	const predicate = value => value > 0;
	const constraint = where(predicate, "ParityPositive");
	expect(where(predicate, "ParityPositive")).toBe(constraint);
	expect(() => where(value => value < 0, "ParityPositive")).toThrow("Runtime.NameRedeclared");
	expect(() => where(42, "ParityNonCallable")).toThrow(TypeError);
});

test("library namespace roots cannot be redeclared", () => {
	expect(() => codes("Issue", "ParityOther")).toThrow("Runtime.NameRedeclared");
	expect(Shapes.decode("$Issue")).toBe(Issue);
});

test("namespace declarations reject portable reserved names before registering members", () => {
	const declarations = {
		Codes: (name, member) => codes(name, "Good", member),
		Slots: (name, member) => Shapes.slots(name, { Good: Text, [member]: Text }),
		Codec: (name, member) => codec(name, { source: Text, canonical: Text, map: { A: "a" }, gaps: { missing: member } }),
		Transform: (name, member) => Shapes.transform(name, value => value, { inputs: [Text], output: Text, gaps: ["Good", member] }),
		Request: (name, member) => Shapes.request(name, { versions: [1], options: {}, validate: (options, value) => value, compile: () => ({}), gaps: ["Good", member] }),
	};
	for (const [index, member] of fixture.reservedNamespaceMembers.entries()) {
		for (const [kind, declare] of Object.entries(declarations)) {
			const name = `ParityReserved${kind}${index}`;
			expect(() => declare(name, member)).toThrow(TypeError);
			expect([...REGISTRY.keys()].some(key => key === name || key.startsWith(name + "."))).toBe(false);
		}
	}
});

test("stage namespaces reject portable reserved names before binding stages", () => {
	for (const [index, member] of fixture.reservedNamespaceMembers.entries()) {
		const name = `ParityReservedStage${index}`;
		expect(() => Combinators.stages(() => ({
			[name]: Combinators.lift(value => value),
			[member]: Combinators.lift(value => value),
		}))).toThrow(TypeError);
		expect(REGISTRY.has(name)).toBe(false);
	}
});

test("case-distinct namespace members remain accessible and encodable", () => {
	const codes = Shapes.codes("ParityValidCodeMembers", ...fixture.validNamespaceMembers);
	const slots = Shapes.slots("ParityValidSlotMembers", Object.fromEntries(fixture.validNamespaceMembers.map(name => [name, Text])));
	for (const name of fixture.validNamespaceMembers) {
		expect(Shapes.encode(codes)[name]).toBe(`$ParityValidCodeMembers.${name}`);
		expect(Shapes.decode(`$ParityValidCodeMembers.${name}`)).toBe(codes[name]);
		expect(slots[name].name).toBe(`ParityValidSlotMembers.${name}`);
	}
});

test("local capture names cannot overwrite capture engine state", () => {
	for (const name of fixture.captureNames) {
		const shape = pattern(`{${name}}`);
		const captures = capture("ok", either(shape, Text));
		expect(captures[name]).toBe("ok");
		expect(evidence(captures, name)).toEqual({ raw: "ok", location: "" });
	}
});

test("codec instance identities normalize JSON numbers", () => {
	const family = codec("ParityFamily", { params: { value: Any }, source: Text, canonical: Text, parse: (argument, value) => value, format: (argument, value) => value });
	for (const item of fixture.codecArguments) {
		const instance = family(item.value);
		expect(instance.name).toBe("ParityFamily" + item.suffix);
		expect(Shapes.decode({ $: "ParityFamily", arguments: [item.value] })).toBe(instance);
	}
	expect(family(1)).toBe(family(1.0));
});

test("failed union branches cannot mutate previously gathered captures", () => {
	const items = slot("ParityUnionItems", [Text]);
	const captures = capture([{ value: "a", ok: 1 }, { value: "b", ok: "bad" }, { value: "c", ok: 1 }],
		[either({ value: gather(items), ok: Int }, Any)]);
	expect(captures[items.symbol]).toEqual(["a", "c"]);
	expect(evidence(captures, items).raw).toEqual(["a", "c"]);
});

test("nested successful unions retain gathered loss and evidence state", () => {
	const items = slot("ParityLossItems", [Text]);
	const conversion = codec("ParityLossCodec", {
		source: either(Text, Int), canonical: Text, losses: ["Discarded"],
		parse: value => lossy(String(value), conversion.Discarded), format: value => value,
	});
	const captures = capture(["a", 1, "c"], [gather(via(items, conversion))]);
	expect(captures[items.symbol]).toEqual(["a", "1", "c"]);
	expect(captureState(captures).losses).toHaveLength(3);
	expect(evidence(captures, items).codec).toEqual(Array(3).fill(conversion.name));
});

test("Any rejects values outside the shared JSON data domain", () => {
	for (const value of [NaN, Infinity, () => null]) expect(errors(value, Any)).not.toEqual([]);
});

test("oversized pure callback outputs have the same defect classification", () => {
	let value = 0;
	for (let index = 0; index < 102; index++) value = [value];
	const conversion = codec("ParityDeepOutput", { source: Text, canonical: Any, parse: () => value, format: () => "" });
	try {
		capture("", via(slot("ParityOutput", Any), conversion));
		throw new Error("expected a failure");
	} catch (failure) {
		expect(failure).toBeInstanceOf(Failure);
		expect(failure.kind).toBe(Failure.Defect);
		expect(failure.code).toBe(Issue.Constraint);
	}
});

test("backoff rejects nonfinite declaration arguments", () => {
	for (const value of [Infinity, NaN, true]) expect(() => Combinators.backoff(value)).toThrow(TypeError);
});

// EOF
