/**
 * Module: hexa.http — `RequestSend` over HTTP for JSON APIs, as a host operation.
 *
 *   const env = { ...http({ base: "https://api.example/v1", fetch: authorisedFetch }), policy: { retry: { default: true, attempts: 3 } } };
 *   const { status, data } = yield env.RequestSend({ method: "GET", path: p`/clients/${id}`, query: { expand: "notes" } });
 *
 * A failed request is a `Failure.Effect` with a `Request` code, transient when a retry may succeed; its
 * message (method, URL, status and the start of the body) is the failure's cause, which the journal keeps.
 */

import { Failure, Issue, isPlain } from "./core.js";
import { codes } from "./shape.js";

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

export const Request = codes("Request", "BadRequest", "Unauthorized", "Forbidden", "NotFound", "Conflict", "Gone", "Rejected",
	"TooManyRequests", "ServerError", "Unavailable", "Timeout", "Network", "Unreadable");

const METHODS = new Set(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]);
const TRANSIENT = new Set([408, 425, 429, 502, 503, 504]);
const CODES = { 400: "BadRequest", 401: "Unauthorized", 403: "Forbidden", 404: "NotFound", 408: "Timeout", 409: "Conflict", 410: "Gone",
	429: "TooManyRequests", 503: "Unavailable", 504: "Timeout" };
const DETAIL_BODY_LENGTH = 500;
const JSON_RE = /[/+]json\b/i;

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

/** The `Request` code of an HTTP error status. */
function requestCode(status) {
	return Request[CODES[status] ?? (status >= 500 ? "ServerError" : "Rejected")];
}

/** The `Failure.Effect` of a failed request, its message (`method url: message`) kept as the cause for the journal. */
function requestFailure(code, transient, method, url, message, cause) {
	const failure = new Failure(Failure.Effect, code, { cause: new Error(`${method} ${url}: ${message}`, cause === undefined ? undefined : { cause }) });
	failure.transient = transient;
	return failure;
}

/** Whether `value` is a query scalar: a string, number or boolean. */
function isScalar(value) {
	return ["string", "number", "boolean"].includes(typeof value);
}

/** Check a request and resolve its URL and fetch options; a malformed request is the stage's contract failure. */
function prepare(request, bases, defaults) {
	const { method = "GET", path, api, query = {}, headers = {}, body } = isPlain(request) ? request : {};
	const base = api == null ? bases[""] : bases[api];
	if (!METHODS.has(method) || typeof path !== "string" || !path.startsWith("/") || typeof base !== "string" || !isPlain(query) || !isPlain(headers)
		|| Object.values(headers).some(value => typeof value !== "string") || Object.values(query).some(value => !(isScalar(value) || Array.isArray(value) && value.every(isScalar)))) {
		throw new Failure(Failure.Contract, Issue.Mismatch);
	}
	const search = new URLSearchParams();
	for (const [key, value] of Object.entries(query)) for (const item of [value].flat()) search.append(key, String(item));
	const encoded = search.toString();
	const url = base.replace(/\/+$/, "") + path + (encoded ? (path.includes("?") ? "&" : "?") + encoded : "");
	const sent = { accept: "application/json", ...defaults, ...headers };
	if (body !== undefined && !Object.keys(sent).some(key => key.toLowerCase() === "content-type")) sent["content-type"] = "application/json";
	return { method, url, init: { method, headers: sent, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) } };
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/**
 * Operations for an environment: `{ RequestSend }`.
 *
 * - `base`: the API's URL; `apis` names further APIs, which a request selects with `api`;
 * - `headers`: sent with every request (a request's own `headers` override them);
 * - `fetch`: a WHATWG `fetch`, e.g. one that adds OAuth credentials (default `globalThis.fetch`);
 * - `timeout`: milliseconds before a request fails as `Request.Timeout`.
 *
 * A `Failure` the `fetch` throws passes through; any other error is `Request.Network` (`Request.Timeout` for a timeout).
 *
 * A request is `{method = "GET", path, api?, query?, headers?, body?}`, `path` starting with `/` (build it with `p`);
 * `body` is sent as JSON. The result is `{status, data}`: `data` is the parsed JSON body, the text of a non-JSON one,
 * or `null` when empty.
 */
export function http({ base, apis = {}, headers = {}, fetch = globalThis.fetch, timeout } = {}) {
	const bases = { ...apis, ...(base !== undefined ? { "": base } : {}) };
	if (typeof fetch !== "function" || Object.values(bases).some(value => typeof value !== "string")) throw new TypeError("http expects base URLs and a fetch function");
	return {
		async RequestSend(request, { signal } = {}) {
			const { method, url, init } = prepare(request, bases, headers);
			const controller = new AbortController();
			const abort = () => controller.abort(signal.reason);
			signal?.addEventListener("abort", abort, { once: true });
			if (signal?.aborted) abort();
			let timedOut = false, response, text;
			const timer = timeout === undefined ? null : setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
			try {
				response = await fetch(url, { ...init, signal: controller.signal });
				text = await response.text();
			} catch (error) {
				if (signal?.aborted || error instanceof Failure) throw error;
				if (timedOut) throw requestFailure(Request.Timeout, true, method, url, `no response within ${timeout} ms`);
				throw requestFailure(Request.Network, true, method, url, String(error?.message ?? error), error);
			} finally {
				if (timer !== null) clearTimeout(timer);
				signal?.removeEventListener("abort", abort);
			}
			if (!response.ok) {
				const excerpt = text ? `: ${text.slice(0, DETAIL_BODY_LENGTH)}` : "";
				throw requestFailure(requestCode(response.status), TRANSIENT.has(response.status), method, url, `${response.status} ${response.statusText}${excerpt}`.trim());
			}
			if (!text) return { status: response.status, data: null };
			if (!JSON_RE.test(response.headers.get("content-type") ?? "")) return { status: response.status, data: text };
			try { return { status: response.status, data: JSON.parse(text) }; }
			catch (error) { throw requestFailure(Request.Unreadable, false, method, url, error.message, error); }
		},
	};
}

// EOF
