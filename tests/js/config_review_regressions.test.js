/** Module: config_review_regressions.test — Configuration boundary regressions. */

import { expect, test } from "bun:test";

import { Failure, Gap, Int, Issue, Profile, SelectionRequest, Text, capture, codec, decode, encode, errors, matches, optional, parsed, reshape, slots, tolerant, via } from "hexa/shapes.js";
import { compileTemplate, outputShape } from "../../src/js/hexa/template.js";

const Fields = slots("JsConfigReview", { Name: Text, Items: [Text] });
const OPTIONS = { versions: [1], maxSelections: 2, maxRelations: 1, entitySets: { People: [] }, relations: ["Membership"] };
const CATALOG = { entitySets: { People: { type: "Person", attributes: {} } }, relations: { Membership: { roles: { member: "Person" } } } };

function failure(fn) {
	try { fn(); } catch (error) { return error; }
	throw new Error("expected failure");
}

test("table codec snapshots caller gap maps", () => {
	const gaps = { unknown: "Blank" };
	const value = codec("JsConfigReviewSnapshot", { source: Text, canonical: Text, map: { A: "a" }, gaps });
	gaps.unknown = "Unmapped";
	gaps.later = "Blank";
	expect(failure(() => value.convert("unknown")).code).toBe(value.Blank);
	expect(failure(() => value.convert("later")).code).toBe(value.Unmapped);
	expect(encode(value).$codec.gaps).toEqual({ unknown: "Blank" });
});

test("fresh decoded table codecs retain examples", () => {
	const encoded = { $codec: { name: "JsConfigReviewExamples", source: "$Text", canonical: "$Text", map: { A: "a" }, gaps: {}, examples: [{ input: "a", output: "A" }] } };
	expect(encode(decode(encoded))).toEqual(encoded);
});

test("value and stage payloads cannot invoke arbitrary registry functions", () => {
	let calls = 0;
	const registry = new Map([["Injected", () => { calls++; }], ["Text", Text]]);
	for (const option of [{ value: 1 }, { stage: "Text" }]) {
		const error = failure(() => decode({ $: "Injected", ...option }, null, { registry }));
		expect([error.kind, error.code]).toEqual([Failure.Contract, Issue.Mismatch]);
	}
	expect(calls).toBe(0);
	expect(failure(() => decode({ $: "Text", value: 1 })).kind).toBe(Failure.Contract);
});

test("reshape keeps local holes but rejects an absent root", () => {
	expect(reshape({ name: tolerant(Fields.Name) }, { Name: Fields.Name, Fixed: 1 }, { name: 3 })).toEqual({ Fixed: 1 });
	expect(reshape(Int, [1, Fields.Name, 3], 2)).toEqual({ 0: 1, 2: 3 });
	expect(() => reshape(Int, Fields.Name, 2)).toThrow(Gap);
});

test("template output shapes infer optional parsed keys and gathered lists", () => {
	const shape = outputShape(compileTemplate({ Name: optional(Fields.Name), Codec: parsed(Fields.Name), Items: [Fields.Items] }));
	expect(errors({ Items: ["a"] }, shape)).toEqual([]);
	const table = codec("JsConfigReviewGather", { source: Int, canonical: Text, parse: String, format: Number });
	const formatted = outputShape(compileTemplate([via(Fields.Items, table)]));
	expect(errors([1, 2], formatted)).toEqual([]);
	expect(errors(["1"], formatted).length).toBeGreaterThan(0);
	expect(capture(["a"], [Fields.Items])[Fields.Items.symbol]).toEqual(["a"]);
});

test("selector captures reject extra reference separators", () => {
	expect(() => matches({ name: "a" }, "{name:$JsConfigReview.Name:$ignored}")).toThrow(TypeError);
});

test("selection validation rejects null relations and accumulates version errors", () => {
	const root = failure(() => SelectionRequest.validate(OPTIONS, null, CATALOG));
	expect(root.kind).toBe(Failure.Contract);
	expect(root.issues).toEqual([{ code: Issue.Mismatch, path: [] }]);
	const missing = failure(() => SelectionRequest.validate(OPTIONS, {}, CATALOG));
	expect(new Set(missing.issues.map(item => item.path.join("/")))).toEqual(new Set(["version", "selections"]));
	const invalid = failure(() => SelectionRequest.validate(OPTIONS, { version: true, selections: {}, relations: null }, CATALOG));
	expect(invalid.issues).toEqual([{ code: Profile.Forbidden, path: ["version"] }, { code: Issue.Constraint, path: ["selections"] }, { code: Issue.Mismatch, path: ["relations"] }]);
	const badSource = failure(() => SelectionRequest.validate(OPTIONS, {
		version: 1, selections: { Person: { from: "Missing", attributes: [] } },
		relations: [{ type: "Membership", participants: { member: "Person" } }],
	}, CATALOG));
	expect(badSource.kind).toBe(Failure.Contract);
	expect(badSource.issues).toHaveLength(2);
});

test("generic profiles do not register the concrete selection format", () => {
	const result = Bun.spawnSync([process.execPath, "--eval", `
		await import("./src/js/hexa/profile.js");
		const { REGISTRY } = await import("./src/js/hexa/core.js");
		if (REGISTRY.has("SelectionRequest")) throw new Error("eager selection registration");
		const { SelectionRequest } = await import("./src/js/hexa/selection_request.js");
		const shapes = await import("./src/js/hexa/shapes.js");
		if (shapes.SelectionRequest !== SelectionRequest || REGISTRY.get("SelectionRequest") !== SelectionRequest) throw new Error("missing registration");
	`], { cwd: process.cwd() });
	expect(result.exitCode).toBe(0);
});

test("implicit catalog paths stop at the first missing link", () => {
	const entry = supplier => ({ correspondences: [{ resolved: true, supplier }] });
	const catalog = { steps: { Root: {}, Leaf: {} }, relations: { Group: entry("Root"), Member: {} },
		entitySets: { People: { type: "Person", attributes: { Name: { ...entry("Leaf"), via: ["Group", "Member"] } } } } };
	const options = { ...OPTIONS, entitySets: { People: ["Name"] }, relations: [] };
	const request = SelectionRequest.validate(options, { version: 1, selections: { P: { from: "People", attributes: ["Name"] } } }, catalog);
	const compiled = SelectionRequest.compile(options, request, catalog);
	expect(compiled.steps).toEqual([{ step: "Root" }]);
	expect(compiled.gaps).toEqual([{ code: SelectionRequest.Unmapped, path: ["selections", "P", "attributes", 0, "via", 1] }]);
	catalog.relations.Member = entry("Root");
	expect(SelectionRequest.compile(options, request, catalog).steps).toEqual([{ step: "Root" }, { step: "Leaf" }]);
});

// EOF
