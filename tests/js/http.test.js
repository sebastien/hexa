/** Module: http.test — `RequestSend` over HTTP, journals of successful runs and failure details. */

import { afterAll, beforeAll, expect, test } from "bun:test";

import { Failure } from "hexa/shapes.js";
import { backoff } from "hexa/combinators.js";
import { Runtime, p, run } from "hexa/runtime.js";
import { Request, http } from "hexa/http.js";

let server, base;

beforeAll(() => {
	server = Bun.serve({ port: 0, async fetch(request) {
		const url = new URL(request.url);
		if (url.pathname === "/v1/clients/7") return Response.json({ id: 7, query: url.search, auth: request.headers.get("authorization") });
		if (url.pathname === "/v1/echo") return Response.json({ method: request.method, type: request.headers.get("content-type"), body: await request.json() });
		if (url.pathname === "/v1/text") return new Response("plain");
		if (url.pathname === "/v1/empty") return new Response(null, { status: 204 });
		if (url.pathname === "/v1/broken") return new Response("{", { headers: { "content-type": "application/json" } });
		return new Response("no such client", { status: 404, statusText: "Not Found" });
	} });
	base = `http://localhost:${server.port}/v1/`;
});

afterAll(() => server.stop(true));

function* Send(env, request) {
	return yield env.RequestSend(request);
}

async function failed(promise) {
	try { await promise; }
	catch (error) { return error; }
	throw new Error("expected failure");
}

test("a request reaches the API with its query and headers and returns the status and parsed data", async () => {
	const env = http({ base, headers: { authorization: "Bearer token" } });
	expect(await run(env, Send, { path: p`/clients/${7}`, query: { expand: ["notes", "reviews"], active: true } })).toEqual({
		status: 200, data: { id: 7, query: "?expand=notes&expand=reviews&active=true", auth: "Bearer token" } });
	expect(await run(env, Send, { method: "POST", path: "/echo", body: { name: "Aroha" } })).toEqual({
		status: 200, data: { method: "POST", type: "application/json", body: { name: "Aroha" } } });
	expect(await run(env, Send, { path: "/text" })).toEqual({ status: 200, data: "plain" });
	expect(await run(env, Send, { path: "/empty" })).toEqual({ status: 204, data: null });
	expect(await run(http({ apis: { crm: base } }), Send, { api: "crm", path: "/clients/7" })).toMatchObject({ status: 200 });
	expect(await run(env, Send, { api: null, path: "/clients/7" })).toMatchObject({ status: 200 });
});

test("an error status is a coded effect failure whose message the journal keeps and replay restores", async () => {
	const journal = [];
	const failure = await failed(run({ ...http({ base }), record: entry => journal.push(entry) }, Send, { path: "/clients/8" }));
	expect([failure.kind, failure.code, failure.transient]).toEqual([Failure.Effect, Request.NotFound, false]);
	expect(journal).toHaveLength(1);
	expect(journal[0].failure).toMatchObject({ kind: "Effect", code: "Request.NotFound" });
	expect(journal[0].failure.detail).toBe(`GET ${base}clients/8: 404 Not Found: no such client`);
	expect(JSON.stringify(failure)).not.toContain("no such client");
	const replayed = await failed(run({ replay: journal }, Send, { path: "/clients/8" }));
	expect([replayed.code, replayed.cause.message]).toEqual([Request.NotFound, journal[0].failure.detail]);
});

test("a successful run's journal reaches the record sink", async () => {
	const journal = [];
	const value = await run({ ...http({ base }), record: entry => journal.push(entry) }, Send, { path: "/clients/7" });
	expect(journal.map(entry => [entry.index, entry.operation, entry.result])).toEqual([[0, "RequestSend", value]]);
	expect(await run({ replay: journal }, Send, { path: "/clients/7" })).toEqual(value);
	expect(await run({ ...http({ base }), record: null }, Send, { path: "/clients/7" })).toEqual(value);
});

test("a failing record sink fails the run at its end without hiding its outcome", async () => {
	const calls = [];
	const broken = entry => { calls.push(entry); throw new Error("audit log down"); };
	const failure = await failed(run({ ...http({ base }), record: broken }, Send, { path: "/clients/7" }));
	expect([failure.kind, failure.code, failure.cause.message]).toEqual([Failure.Defect, Runtime.RecordFailed, "audit log down"]);
	expect([calls.length, failure.journal.length]).toEqual([1, 1]);
	expect((await failed(run({ ...http({ base }), record: broken }, Send, { path: "/clients/8" }))).code).toBe(Request.NotFound);
});

test("transient statuses and network failures are retried under the retry policy", async () => {
	const answers = [new Response("busy", { status: 503 }), Response.json({ ok: true })];
	const journal = [];
	const env = { ...http({ base, fetch: async () => answers.shift() }), record: entry => journal.push(entry),
		policy: { retry: { default: true, attempts: 2, delay: backoff(0) } } };
	expect(await run(env, Send, { path: "/clients/7" })).toEqual({ status: 200, data: { ok: true } });
	expect(journal.map(entry => [entry.operation, entry.failure?.code ?? null, entry.failure?.transient ?? null])).toEqual([
		["RequestSend", "Request.Unavailable", true], ["DelayWait", null, null], ["RequestSend", null, null]]);
	const network = await failed(run(http({ base, fetch: async () => { throw new TypeError("fetch failed"); } }), Send, { path: "/clients/7" }));
	expect([network.code, network.transient, network.cause.message]).toEqual([Request.Network, true, `GET ${base}clients/7: fetch failed`]);
	const refusing = await failed(run(http({ base, fetch: async () => { throw new Failure(Failure.Effect, Request.Forbidden); } }), Send, { path: "/clients/7" }));
	expect(refusing.code).toBe(Request.Forbidden);
});

test("a timeout is a transient effect failure, and cancelling the run aborts the request", async () => {
	const hang = (url, { signal }) => new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
	const timeout = await failed(run(http({ base, fetch: hang, timeout: 5 }), Send, { path: "/clients/7" }));
	expect([timeout.code, timeout.transient]).toEqual([Request.Timeout, true]);
	const controller = new AbortController();
	const pending = failed(run({ ...http({ base, fetch: hang }), signal: controller.signal }, Send, { path: "/clients/7" }));
	controller.abort();
	expect((await pending).kind).toBe(Failure.Cancel);
});

test("malformed requests and unreadable JSON fail", async () => {
	for (const request of [{ path: "clients/7" }, { method: "TRACE", path: "/clients/7" }, { api: "other", path: "/clients/7" }, { path: "/x", query: { a: { b: 1 } } }]) {
		expect((await failed(run(http({ base }), Send, request))).kind).toBe(Failure.Contract);
	}
	const broken = await failed(run(http({ base }), Send, { path: "/broken" }));
	expect([broken.kind, broken.code, broken.transient]).toEqual([Failure.Effect, Request.Unreadable, false]);
});

// EOF
