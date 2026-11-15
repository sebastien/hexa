/** Module: conformance.test — Shared Python/JavaScript mortgage fixtures. */

import { readFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";

import { expect, test } from "bun:test";

import { encode, decode } from "hexa/shapes.js";
import { pipe, select } from "hexa/combinators.js";
import { inert, run } from "hexa/runtime.js";
import * as Example from "../../examples/mortgage-people/people_extract.js";

const fixture = async name => JSON.parse(await readFile(new URL(`../../examples/mortgage-people/fixtures/${name}.json`, import.meta.url), "utf8"));

test("live mortgage extraction matches the shared expected fixture", async () => {
	expect(await run(Example.peopleExtractEnvironment(await fixture("responses")), Example.People.PeopleExtract)).toEqual(await fixture("expected"));
});

test("Python journal replays without host operations", async () => {
	expect(await run({ root: { mortgage: 42 }, replay: await fixture("journal"), RequestSend() { throw new Error("host called"); } }, Example.People.PeopleExtract)).toEqual(await fixture("expected"));
});

test("effect origins survive host-side composition", async () => {
	const occurrences = await run({ root: { mortgage: 42 }, replay: await fixture("journal") }, pipe(Example.People.MortgageRetrieve, select("clients/*")));
	expect(occurrences[0].origin).toEqual({ strand: "/Clients", seq: 0, pointer: "/data/0" });
});

test("inert operations describe effects without executing", () => {
	const env = inert({ root: { mortgage: 42 } });
	const iterator = Example.People.MortgageRetrieve(env);
	const instruction = iterator.next().value;
	expect(encode(instruction.items.Application)).toEqual({ operation: "RequestSend", arguments: [{ method: "GET", path: "/mortgages/42" }] });
	iterator.return();
});

test("shape encodings match shared fixtures unchanged", async () => {
	// Example declarations stay private; decode resolves their registered names.
	for (const [name, expected] of Object.entries(await fixture("shapes"))) {
		expect(encode(decode(expected))).toEqual(expected);
		if (["CountryName", "Client"].includes(name)) expect(encode(decode("$" + name))).toEqual(expected);
	}
});

test("Node runs the published package entry points without Bun globals", async () => {
	const output = execFileSync("node", ["examples/mortgage-people/run.js"], { cwd: new URL("../..", import.meta.url), encoding: "utf8" });
	expect(JSON.parse(output)).toEqual(await fixture("expected"));
});

// EOF
