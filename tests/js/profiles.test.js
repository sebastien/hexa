/** Module: profiles.test — Permission-first decoding and request planning. */

import { expect, test } from "bun:test";

import { DateOnly, DatePattern, DateTime, Failure, Int, Issue, Positive, Profile, SelectionRequest, Text, capture, decode, describe, either, encode, loose, profile, refine, slots } from "hexa/shapes.js";
import { pipe, plan, validate } from "hexa/combinators.js";
import { run } from "hexa/runtime.js";

const Fields = slots("JsProfileFields", { Id: refine(Int, Positive), Name: Text, Born: refine(DateTime, DateOnly) });
const Hidden = slots("JsProfileHidden", { Secret: Text });
const SELECTION_OPTIONS = { versions: [1], maxSelections: 2, maxRelations: 1, entitySets: { Clients: ["GivenNames", "Household", "Income"], Households: [] }, relations: ["HouseholdHasMemberPerson"] };
const DEFINITION = {
	constructors: [loose, either, refine], types: [Text, Int],
	shapes: { JsProfileRow: loose({ id: Fields.Id }) }, slots: [Fields], constraints: [Positive], codecs: [DatePattern],
	requests: new Map([[SelectionRequest, SELECTION_OPTIONS]]), budgets: { maxDepth: 8, maxNodes: 200 },
};
const Caller = profile("JsProfileCaller", DEFINITION);
const CATALOG = {
	steps: {
		MortgageRetrieve: { capability: "mortgage_read" },
		ClientsRetrieve: { capability: "client_read", requires: ["MortgageRetrieve"] },
		HouseholdsRetrieve: { capability: "household_read", requires: ["MortgageRetrieve"] },
		IncomeRetrieve: { capability: "income_read" },
	},
	entitySets: {
		Clients: { type: "Person", requires: ["MortgageRetrieve"], attributes: {
			GivenNames: { correspondences: [{ supplier: "ClientsRetrieve", resolved: true }] },
			Household: { correspondences: [{ supplier: "HouseholdsRetrieve", resolved: true }], reference: "Household" },
			Income: { correspondences: [{ supplier: "IncomeRetrieve", resolved: false }] },
			Secret: { correspondences: [{ supplier: "ClientsRetrieve", resolved: true }] },
		} },
		Households: { type: "Household", attributes: {} },
		Advisers: { type: "Person", attributes: {} },
	},
	relations: { HouseholdHasMemberPerson: { roles: { source: "Household", target: "Person" }, correspondences: [] } },
};

function refused(value) {
	try { decode(value, Caller); }
	catch (error) { return error; }
	throw new Error("expected refusal");
}
function prepare(value, policy = {}, catalog = CATALOG) {
	return run({ policy: { profile: Caller, ...policy }, vocabulary: { SelectionRequest: catalog } }, pipe(validate(SelectionRequest), plan(SelectionRequest)), value);
}
async function failure(promise) {
	try { await promise; }
	catch (error) { return error; }
	throw new Error("expected failure");
}

test("profiles admit only declared terms and codec family instances", () => {
	const shape = decode({ $loose: {
		id: "$JsProfileFields.Id",
		born: { $: "JsProfileFields.Born", via: { $: "DatePattern", arguments: ["YYYYMMDD"] } },
		kind: { $either: ["$Text", "$Int"] },
	} }, Caller);
	expect(capture({ id: 1, born: "20260915", kind: 2 }, shape)[Fields.Born.symbol]).toBe("2026-09-15");
	expect(decode("$JsProfileRow", Caller)).toBe(decode("$JsProfileRow"));
	expect(encode(decode({ $refine: "$Int", constraints: ["$Positive"] }, Caller))).toEqual({ $refine: "$Int", constraints: ["$Positive"] });
});

test("all forbidden references are collected without disclosing the registry", () => {
	const failure = refused({ a: "$JsProfileHidden.Secret", b: "$Bool", c: "$Unknown", d: { $tuple: ["$Text"] } });
	expect(failure.kind).toBe(Failure.Contract);
	expect(failure.code).toBe(Profile.Forbidden);
	expect(failure.issues.map(issue => issue.path)).toEqual([["a"], ["b"], ["c"], ["d", "$tuple"]]);
	expect(refused("$JsProfileHidden.Secret").issues).toEqual(refused("$JsUnknown").issues);
	for (const value of [
		{ $: "Policy.Substitute", value: 1 }, { operation: "Read", arguments: [] },
		{ $codec: { name: "Injected", source: "$Text", canonical: "$Text", map: {} } },
		{ $pattern: "{JsProfileHidden.Secret}" }, encode(Caller), "$Policy.Drop",
	]) expect(refused(value).code).toBe(Profile.Forbidden);
	expect(Hidden.Secret.name).toBe("JsProfileHidden.Secret");
});

test("permissions precede construction and profile budgets apply", () => {
	expect(refused({ $loose: "$Text" }).code).toBe(Issue.Mismatch);
	expect(refused(Object.fromEntries(Array.from({ length: 150 }, (_, index) => [index, "$Text"]))).code).toBe(Profile.Exceeded);
	expect(() => profile("JsProfileLarge", { budgets: { maxDepth: 1e6 } })).toThrow(TypeError);
	expect(profile("JsProfileCaller", DEFINITION)).toBe(Caller);
	expect(() => profile("JsProfileCaller", { types: [Text] })).toThrow("Runtime.NameRedeclared");
	expect(() => profile("JsProfileBad", { constructors: [capture] })).toThrow(TypeError);
});

test("profiles round trip and descriptions do not leak hidden terms", () => {
	const encoded = encode(Caller);
	expect(encoded.$profile.slots).toEqual(["$JsProfileFields"]);
	expect(decode(encoded)).toBe(Caller);
	expect(() => decode({ $profile: { ...encoded.$profile, types: ["$Text"] } })).toThrow("Issue.Mismatch");
	expect(decode({ $profile: { ...encoded.$profile, name: "JsProfileFresh", budgets: { maxDepth: 4 } } }).limits.maxDepth).toBe(4);
	const description = describe(Caller);
	expect(Object.keys(description.slots)).toEqual(["JsProfileFields.Id", "JsProfileFields.Name", "JsProfileFields.Born"]);
	expect(description.types).toEqual(["$Text", "$Int"]);
	expect(JSON.stringify(description)).not.toContain("JsProfileHidden");
	expect(description.requests.SelectionRequest.gaps).toEqual(["Unmapped", "Unresolved", "NotSupplied", "ReferenceUnselected"]);
});

test("selection requests compile dependencies, capabilities and plan gaps", async () => {
	const request = {
		version: 1,
		selections: { People: { from: "Clients", attributes: ["GivenNames", "Household", "Income"] }, Home: { from: "Households", attributes: [] } },
		relations: [{ type: "HouseholdHasMemberPerson", participants: { source: "Home", target: "People" } }],
	};
	const result = await prepare(request);
	expect(result.steps).toEqual([{ step: "MortgageRetrieve" }, { step: "ClientsRetrieve" }, { step: "HouseholdsRetrieve" }]);
	expect(result.allow).toEqual(["mortgage_read", "client_read", "household_read"]);
	expect(result.gaps).toEqual([
		{ code: "SelectionRequest.Unresolved", path: ["selections", "People", "attributes", 2] },
		{ code: "SelectionRequest.Unmapped", path: ["relations", 0] },
	]);
	expect(result.request.relations[0].participants).toEqual({ source: ["Home"], target: ["People"] });
	expect(result.budgets.maxGaps).toBe(1000);
	expect(await prepare(request)).toEqual(result);
});

test("selection plans retain catalog order while adding transitive dependencies", async () => {
	const catalog = { ...CATALOG, steps: {
		ClientsRetrieve: { capability: "client_read", requires: ["Middle", "MortgageRetrieve"] },
		Independent: { capability: "independent_read" },
		Middle: { capability: "middle_read", requires: ["MortgageRetrieve"] },
		MortgageRetrieve: { capability: "mortgage_read" },
		Unused: { requires: ["Unused"] },
	}, entitySets: { ...CATALOG.entitySets, Clients: { ...CATALOG.entitySets.Clients, requires: ["Independent"] } } };
	const request = { version: 1, selections: { People: { from: "Clients", attributes: ["GivenNames"] } } };
	const result = await prepare(request, {}, catalog);
	expect(result.steps).toEqual(["ClientsRetrieve", "Independent", "Middle", "MortgageRetrieve"].map(step => ({ step })));
	expect(result.allow).toEqual(["client_read", "independent_read", "middle_read", "mortgage_read"]);
});

test("selection plans reject missing dependencies as defects", async () => {
	const request = { version: 1, selections: { People: { from: "Clients", attributes: ["GivenNames"] } } };
	const steps = { ClientsRetrieve: { requires: ["Missing"] }, MortgageRetrieve: {} };
	const result = await failure(prepare(request, {}, { ...CATALOG, steps }));
	expect([result.kind, result.code]).toEqual([Failure.Defect, Issue.Missing]);
});

test("unselected reference targets become explicit plan gaps", async () => {
	const result = await prepare({ version: 1, selections: { People: { from: "Clients", attributes: ["Household"] } } });
	expect(result.gaps).toEqual([{ code: "SelectionRequest.ReferenceUnselected", path: ["selections", "People", "attributes", 0] }]);
});

test("request refusals preserve exact classification and paths", async () => {
	for (const [request, code, path] of [
		[{ selections: {} }, Issue.Missing, ["version"]],
		[{ version: 2, selections: {} }, Profile.Forbidden, ["version"]],
		[{ version: 1, selections: { People: { from: "Advisers", attributes: [] } } }, Profile.Forbidden, ["selections", "People", "from"]],
		[{ version: 1, selections: { People: { from: "Clients", attributes: ["Secret"] } } }, Profile.Forbidden, ["selections", "People", "attributes", 0]],
		[{ version: 1, selections: { people: { from: "Clients", attributes: [] } } }, Issue.Mismatch, ["selections", "people"]],
		[{ version: 1, selections: {} }, Issue.Constraint, ["selections"]],
		[{ version: 1, selections: { People: { from: "Clients", attributes: [] } }, extra: 1 }, Issue.Unexpected, ["extra"]],
		[{ version: 1, selections: { People: { from: "Clients", attributes: ["Income", "Income"] } } }, Issue.Constraint, ["selections", "People", "attributes"]],
	]) {
		const result = await failure(prepare(request));
		expect([result.kind, result.code, result.path]).toEqual([Failure.Contract, code, path]);
	}
});

test("host capabilities and request budgets cannot be bypassed", async () => {
	const request = { version: 1, selections: { People: { from: "Clients", attributes: ["GivenNames"] } } };
	expect((await failure(prepare(request, { allow: ["mortgage_read"] }))).code).toBe(Profile.Forbidden);
	expect((await prepare(request, { allow: ["mortgage_read", "client_read"] })).steps).toHaveLength(2);
	expect((await failure(run({}, validate(SelectionRequest), request))).code).toBe(Profile.Forbidden);
	expect((await failure(run({ policy: { profile: "JsProfileCaller" } }, validate(SelectionRequest), request))).code).toBe(Issue.Mismatch);
	const oversized = { version: 1, selections: { People: { from: "Clients", attributes: Array(300).fill("GivenNames") } } };
	expect((await failure(prepare(oversized))).code).toBe(Profile.Exceeded);
});

// EOF
