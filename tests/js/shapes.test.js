/** Module: shapes.test — Matching, evidence, templates and JavaScript boundaries. */

import { expect, test } from "bun:test";

import { Any, Bool, DateOnly, DatePattern, DateTime, Decimal, DecimalText, Failure, Float, Gap, Instant, InstantOffset, Int, Issue, Null, Positive, Text, Tuple, __, capture, codec, codes, decode, derive, either, encode, errors, evidence, expand, gather, loose, matches, optional, parsed, pattern, refine, rest, reshape, slot, slots, tolerant, transform, via, where } from "hexa/shapes.js";
import { Runtime } from "hexa/runtime.js";
import { validateData } from "../../src/js/hexa/core.js";
import { captureState } from "../../src/js/hexa/shape.js";

const Fields = slots("JsShape", { Id: Int, Name: Text, Country: Text, Items: [Text], Date: refine(DateTime, DateOnly) });
const key = slot => slot.symbol;

test("numeric types and normalization", () => {
	for (const value of [1, -0, Number.MAX_SAFE_INTEGER]) expect(errors(value, Int)).toEqual([]);
	for (const value of [true, "1", 1.5, 2 ** 53, Infinity, NaN, new Number(1)]) expect(errors(value, Int).length).toBeGreaterThan(0);
	expect(errors(1, Float).length).toBe(1);
	expect(errors(1.5, Float)).toEqual([]);
	expect(errors(1, Bool).length).toBe(1);
	expect(errors(true, 1).length).toBe(1);
	expect(Object.is(capture(-0, Fields.Id)[key(Fields.Id)], -0)).toBe(false);
});

test("struct issues and optional undefined", () => {
	expect(errors({ b: null, c: 1 }, { a: Text, "b?": Text }).map(issue => issue.code)).toEqual([Issue.Missing, Issue.Mismatch, Issue.Unexpected]);
	expect(errors({ b: undefined }, { "b?": Text })).toEqual([]);
	expect(errors(undefined, Null).length).toBe(1);
	expect(() => errors(1, undefined)).toThrow(TypeError);
	expect(errors({ a: "ok", extra: 1 }, loose({ a: Text }))).toEqual([]);
	expect(errors(Object.assign(Object.create(null), { a: "ok" }), { a: Text })).toEqual([]);
	expect(errors(new Date(), {} ).length).toBe(1);
});

test("slot interning, immutability and checked namespaces", async () => {
	expect(slot("JsShape.Id", Int)).toBe(Fields.Id);
	expect(() => slot("JsShape.Id", Text)).toThrow("Runtime.SlotRedeclared");
	expect(() => Fields.Unknown).toThrow("Runtime.SlotUnknown");
	expect(() => { Fields.Id = Text; }).toThrow(TypeError);
	expect(Fields.then).toBeUndefined();
	expect(Fields.toJSON).toBeUndefined();
	expect(Fields.constructor).toBeUndefined();
	expect(await Promise.resolve(Fields)).toBe(Fields);
	expect(Fields.Id.symbol).toBe(Symbol.for("slot:JsShape.Id"));
	expect(capture({ a: 1, b: 1 }, { a: Fields.Id, b: Fields.Id })[key(Fields.Id)]).toBe(1);
	expect(() => capture({ a: 1, b: 2 }, { a: Fields.Id, b: Fields.Id })).toThrow("Issue.CaptureConflict");
});

test("reason declarations cannot overwrite registered slots", () => {
	const existing = slot("JsCollision.Value", Text);
	expect(() => codes("JsCollision", "Value")).toThrow("Runtime.NameRedeclared");
	expect(decode("$JsCollision.Value")).toBe(existing);
});

test("tuples, rest and collection evidence", () => {
	expect(errors([1, 2, null, true, "end"], Tuple(Int, Int, __, Text))).toEqual([]);
	expect(errors([1], Tuple(Int, __, Text)).length).toBe(1);
	const captures = capture([1, "a", "b", 2], Tuple(Int, rest(Fields.Items), Int));
	expect(captures[key(Fields.Items)]).toEqual(["a", "b"]);
	expect(evidence(captures, Fields.Items)).toEqual({ raw: ["a", "b"], location: ["/1", "/2"] });
	expect(capture([1, 2], Tuple(Int, rest(Fields.Items), Int))[key(Fields.Items)]).toEqual([]);
	expect(() => Tuple(__, __)).toThrow(TypeError);
	expect(() => capture([], rest(Any))).toThrow(TypeError);
	expect(errors([, 1], [Int]).length).toBe(1);
});

test("collection captures and two-way expansion", () => {
	const captures = capture(["a", "b"], [Fields.Items]);
	expect(expand([Fields.Items], captures)).toEqual([["a", "b"], []]);
	expect(expand(decode(encode([Fields.Items])), captures)).toEqual([["a", "b"], []]);
	const shape = { "rows?": [loose({ "name?": gather(Fields.Items) })] };
	expect(Object.hasOwn(capture({}, shape), key(Fields.Items))).toBe(false);
	expect(capture({ rows: [] }, shape)[key(Fields.Items)]).toEqual([]);
	expect(capture({ rows: [{}, { name: "a" }] }, shape)[key(Fields.Items)]).toEqual(["a"]);
	expect(() => capture([], [{ a: gather(Fields.Items), b: gather(Fields.Items) }])).toThrow(TypeError);
});

test("table codecs preserve original spelling and evidence", () => {
	const country = codec("JsCountry", { source: Text, canonical: Text, map: { NZ: ["New Zealand", "Aotearoa"] }, gaps: { Unknown: "NotStated" } });
	const captures = capture("Aotearoa", via(Fields.Country, country));
	expect(captures[key(Fields.Country)]).toBe("NZ");
	expect(expand(via(Fields.Country, country), captures)[0]).toBe("Aotearoa");
	expect(expand(parsed(Fields.Country), captures)[0]).toBe("JsCountry");
	expect(evidence(captures, Fields.Country)).toEqual({ raw: "Aotearoa", location: "", codec: "JsCountry" });
	expect(() => capture("Unknown", via(Fields.Country, country))).toThrow("JsCountry.NotStated");
	expect(() => capture("Narnia", via(Fields.Country, country))).toThrow("JsCountry.Unmapped");
	expect(() => codec("JsDuplicate", { source: Text, canonical: Text, map: { a: "same", b: "same" } })).toThrow(TypeError);
	const detached = evidence(captures, Fields.Country);
	detached.raw = "changed";
	expect(evidence(captures, Fields.Country).raw).toBe("Aotearoa");
});

test("tolerant positions and holes retain indexes as maps", () => {
	const captures = capture({ name: 3 }, { name: tolerant(Fields.Name), "country?": Fields.Country });
	expect(expand({ Name: optional(Fields.Name), Country: optional(Fields.Country), Fixed: 1 }, captures)).toEqual([{ Fixed: 1 }, [{ path: ["Name"], reason: Issue.Mismatch }]]);
	expect(expand([10, Fields.Name, 30], {})).toEqual([{ 0: 10, 2: 30 }, [{ path: [1], reason: Issue.Missing }]]);
	expect(() => expand(Fields.Name, {})).toThrow(Gap);
	const gathered = capture([{ name: "a" }, { name: 3 }, { name: "b" }], [{ name: tolerant(gather(Fields.Items)) }]);
	expect(Object.hasOwn(gathered, key(Fields.Items))).toBe(false);
});

test("expansion copies data and merges enclosing captures", () => {
	const Mutable = slots("JsMutable", { Data: Any });
	const original = { a: [1] }, captures = capture(original, Mutable.Data);
	const [first] = expand({ one: Mutable.Data, two: Mutable.Data }, captures);
	first.one.a.push(2);
	expect(original).toEqual({ a: [1] });
	expect(first.two).toEqual(original);
	expect(reshape({ name: Fields.Name }, { Name: Fields.Name, Id: Fields.Id }, { name: "Ada" }, { [key(Fields.Id)]: 7 })).toEqual({ Name: "Ada", Id: 7 });
	expect(() => reshape({ name: Fields.Name }, Fields.Name, { name: "Ada" }, { [key(Fields.Name)]: "Bea" })).toThrow("Issue.CaptureConflict");
});

test("derivations validate signatures and declared gaps", () => {
	const compose = transform("JsCompose", (name, country) => name + "/" + country, { inputs: [Text, Text], output: Text });
	const captures = capture({ name: "A", country: "NZ" }, { name: Fields.Name, country: Fields.Country });
	expect(expand({ Display: derive(compose, Fields.Name, Fields.Country) }, captures)).toEqual([{ Display: "A/NZ" }, []]);
	expect(() => derive(compose, Fields.Id, Fields.Country)).toThrow(TypeError);
	expect(() => derive(compose, 3, "NZ")).toThrow(TypeError);
	expect(expand({ Display: derive(compose, Fields.Name, Fields.Country) }, {})[1]).toHaveLength(1);
	const checked = transform("JsChecked", () => { throw new Gap(checked.Blank); }, { inputs: [], output: Text, gaps: ["Blank"] });
	expect(expand({ value: derive(checked) }, {})[1]).toEqual([{ path: ["value"], reason: checked.Blank }]);
	expect(() => checked.Other).toThrow("Runtime.MemberUnknown");
	const joined = transform("JsJoined", (...values) => values.join("/"), { inputs: [rest(Text)], output: Text });
	expect(expand(derive(joined, "a", "b"), {})[0]).toBe("a/b");
});

test("union attempts are transactional", () => {
	const Union = slots("JsUnion", { First: Text, Second: Text });
	const captures = capture({ a: "ok", b: 1 }, either({ a: Union.First, b: Text }, { a: Union.Second, b: Int }));
	expect(Object.hasOwn(captures, key(Union.First))).toBe(false);
	expect(captures[key(Union.Second)]).toBe("ok");
});

test("exact dates, decimals and nanosecond instant offsets", () => {
	for (const value of ["2026-11-10", "2024-02-29", "2026-11-10T14:30:05.123+13:00"]) expect(errors(value, DateTime)).toEqual([]);
	for (const value of ["2026-02-29", "2026-11-10Z", "2026-11-10T24:00", "2026-W01-1", "2026-11-10T23:59:60Z"]) expect(errors(value, DateTime).length).toBe(1);
	expect(capture("20260915", via(Fields.Date, DatePattern("YYYYMMDD")))[key(Fields.Date)]).toBe("2026-09-15");
	expect(DatePattern("YYYYMMDD")).toBe(DatePattern("YYYYMMDD"));
	expect(DatePattern("YYYYMMDD").family).toBe(DatePattern);
	const Exact = slots("JsExact", { Amount: Decimal, Instant: Instant });
	for (const [raw, expected] of [["1.2500e3", "1250"], ["-0.000", "0"], ["0.00100", "0.001"], [12.5, "12.5"]]) expect(capture(raw, via(Exact.Amount, DecimalText))[key(Exact.Amount)]).toBe(expected);
	const instant = capture("2026-01-01T00:30:00.123456789+01:00", via(Exact.Instant, InstantOffset));
	expect(instant[key(Exact.Instant)]).toBe("2025-12-31T23:30:00.123456789Z");
	expect(captureState(instant).losses[0].reason).toBe(InstantOffset.Discarded);
});

test("patterns and enclosing selector captures", () => {
	const integer = codec("JsIntegerText", { source: Text, canonical: Int, parse: value => Number(value), format: value => String(value) });
	const captures = capture("Ada-42", pattern("{Name}-{JsShape.Id:JsIntegerText}"));
	expect(captures.Name).toBe("Ada");
	expect(captures[key(Fields.Id)]).toBe(42);
	expect(evidence(captures, Fields.Id).codec).toBe(integer.name);
	expect(errors("Ada\n", pattern("Ada")).length).toBe(1);
	const selected = matches({ parents: [{ id: 3, children: ["a", "b"] }] }, "parents/*/{id:$JsShape.Id}/children/*");
	expect(selected.map(item => item.location)).toEqual(["/parents/0/children/0", "/parents/0/children/1"]);
	expect(selected[0].captures[key(Fields.Id)]).toBe(3);
	expect(matches({ "a/b": { "~": [1] } }, "a~1b/~0/*")[0].location).toBe("/a~1b/~0/0");
});

test("encoding round trips and malformed configurations", () => {
	for (const value of [Tuple(Int, __, Text), refine(Int, Positive), loose({ id: Fields.Id }), tolerant(Fields.Name), parsed(Fields.Name), pattern("{Name}"), either(Text, Int), via(Fields.Date, DatePattern("YYYYMMDD"))]) expect(encode(decode(encode(value)))).toEqual(encode(value));
	expect(expand(decode({ "$$literal?": "$$Text" }), {})[0]).toEqual({ "$literal?": "$Text" });
	// A verbatim key is never optional: an optional `$` key has no encoding that decodes back to it.
	expect(encode(loose({ $id: Fields.Id }))).toEqual({ $loose: { $$id: "$JsShape.Id" } });
	for (const unencodable of [loose({ "$id?": Fields.Id }), { "$id?": "$x" }]) expect(() => encode(unencodable)).toThrow("an optional key cannot start with '$'");
	for (const malformed of ["$Unknown", { $loose: "$Text" }, { $refine: "$Int", constraints: ["$Text"] }, { $unknown: 1 }, { $: "Text", arguments: [1] }]) expect(() => decode(malformed)).toThrow(Error);
	expect(() => decode({ $rest: "$Any", unexpected: 1 })).toThrow("Issue.Unexpected");
	expect(() => decode("$Text", null, { registry: new Map() })).toThrow("Runtime.MemberUnknown");
});

test("codec and predicate defects cannot masquerade as gaps", () => {
	const bad = codec("JsBadCodec", { source: Text, canonical: Text, parse: () => 3, format: value => value });
	expect(() => capture("source", via(Fields.Name, bad))).toThrow("Issue.Mismatch");
	expect(() => capture("1", via(Fields.Id, DecimalText))).toThrow(TypeError);
	const asyncConstraint = where(async () => true, "JsAsyncConstraint");
	expect(() => errors(1, refine(Int, asyncConstraint))).toThrow("Runtime.NativeAwaitable");
	expect(Failure.Gap).toBe(Symbol.for("member:Failure.Gap"));
	expect(Runtime.MemberUnknown).toBe(Symbol.for("member:Runtime.MemberUnknown"));
	expect(() => Failure.Unknown).toThrow("Runtime.MemberUnknown");
	expect(() => { Failure.Gap = "changed"; }).toThrow(TypeError);
});

test("codec declarations round trip and conflicting definitions are refused", () => {
	const country = codec("JsEncodedCountry", { source: Text, canonical: Text, map: { NZ: ["New Zealand", "Aotearoa"] }, gaps: { Unknown: "NotStated" } });
	const encoded = encode(country);
	expect(encoded.$codec.map).toEqual({ NZ: ["New Zealand", "Aotearoa"] });
	expect(decode(encoded)).toBe(country);
	const altered = structuredClone(encoded);
	altered.$codec.map.AU = "Australia";
	expect(() => decode(altered)).toThrow("Issue.Mismatch");
	altered.$codec.name = "JsDecodedCountry";
	const declared = decode(altered);
	expect(capture("Australia", via(Fields.Country, declared))[Fields.Country.symbol]).toBe("AU");
	expect(encode(decode(encode(DatePattern)))).toEqual(encode(DatePattern));
});

test("data boundaries reject unsafe keys, cycles, accessors and unsupported values", () => {
	const cycle = []; cycle.push(cycle);
	for (const value of [cycle, [, 1], undefined, NaN, Infinity, 1n, new Date(), new String("x"), { [Symbol("x")]: 1 }, JSON.parse('{"__proto__":1}'), { constructor: 1 }, "\uD800"]) expect(() => validateData(value)).toThrow(Error);
	let invoked = false;
	const accessor = { get value() { invoked = true; return 1; } };
	expect(() => validateData(accessor)).toThrow(Error);
	const array = [];
	Object.defineProperty(array, 0, { get() { invoked = true; return 1; }, enumerable: true });
	expect(() => validateData(array)).toThrow(Error);
	expect(errors(array, [Int])).toHaveLength(1);
	expect(errors(accessor, { value: Int })).toHaveLength(1);
	expect(invoked).toBe(false);
	expect(() => validateData([[[1]]], { maxDepth: 2 })).toThrow("Issue.Constraint");
	expect(() => validateData("abc", { maxBytes: 2 })).toThrow("Issue.Constraint");
	const shared = { a: [] }; validateData([shared, shared]);
});

// EOF
