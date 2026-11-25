"""Module: hexa.http — `RequestSend` over HTTP for JSON APIs, as a host operation.

	host = {**http("https://api.example/v1", headers={"authorization": "Bearer …"}), "policy": {"retry": {"default": True, "attempts": 3}}}
	# Within a stage receiving its runtime environment as `env`:
	response = yield env.RequestSend({"method": "GET", "path": p(t"/clients/{id}"), "query": {"expand": "notes"}})

A failed request is a `Failure.Effect` with a `Request` code, transient when a retry may succeed; its message
(method, URL, status and the start of the body) is the failure's cause, which the journal keeps.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from urllib.parse import quote_plus

from .core import Failure, Issue
from .shape import codes

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["Request", "http"]

Request = codes("Request", "BadRequest", "Unauthorized", "Forbidden", "NotFound", "Conflict", "Gone", "Rejected",
	"TooManyRequests", "ServerError", "Unavailable", "Timeout", "Network", "Unreadable")

METHODS = frozenset(("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"))
TRANSIENT = frozenset((408, 425, 429, 502, 503, 504))
CODES = {400: "BadRequest", 401: "Unauthorized", 403: "Forbidden", 404: "NotFound", 408: "Timeout", 409: "Conflict", 410: "Gone",
	429: "TooManyRequests", 503: "Unavailable", 504: "Timeout"}
DETAIL_BODY_LENGTH = 500
JSON_RE = re.compile(r"[/+]json\b", re.IGNORECASE)

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------


def requestCode(status: int):
	"""The `Request` code of an HTTP error status."""
	return getattr(Request, CODES.get(status, "ServerError" if status >= 500 else "Rejected"))


def requestFailure(code, transient: bool, method: str, url: str, message: str, cause: BaseException | None = None) -> Failure:
	"""The `Failure.Effect` of a failed request, its message (`method url: message`) kept as the cause for the journal."""
	error = Exception(f"{method} {url}: {message}")
	error.__cause__ = cause
	failure = Failure(Failure.Effect, code)
	failure.transient = transient
	failure.__cause__ = error
	return failure


def isScalar(value) -> bool:
	"""Whether `value` is a query scalar: a string, number or boolean."""
	return isinstance(value, (str, int, float))


def formText(value) -> str:
	"""A query value as JavaScript's `String` spells it, form-encoded as `URLSearchParams` encodes it."""
	if isinstance(value, bool):
		value = "true" if value else "false"
	elif isinstance(value, float) and value.is_integer():
		value = str(int(value))
	return quote_plus(str(value), safe="*").replace("~", "%7E")


def prepare(request: object, bases: Mapping[str, str], defaults: Mapping[str, str]) -> tuple[str, str, dict, bytes | None]:
	"""Check a request and resolve its URL, headers and body; a malformed request is the stage's contract failure."""
	request = request if isinstance(request, dict) else {}
	method, path, query, headers = request.get("method", "GET"), request.get("path"), request.get("query", {}), request.get("headers", {})
	base = bases.get("" if request.get("api") is None else request["api"])
	if (method not in METHODS or not isinstance(path, str) or not path.startswith("/") or not isinstance(base, str)
		or not isinstance(query, dict) or not isinstance(headers, dict) or any(not isinstance(value, str) for value in headers.values())
		or any(not (isScalar(value) or isinstance(value, list) and all(map(isScalar, value))) for value in query.values())):
		raise Failure(Failure.Contract, Issue.Mismatch)
	encoded = "&".join(f"{formText(key)}={formText(item)}" for key, value in query.items() for item in (value if isinstance(value, list) else [value]))
	url = base.rstrip("/") + path + (("&" if "?" in path else "?") + encoded if encoded else "")
	sent = {"accept": "application/json", **defaults, **headers}
	body = None
	if "body" in request:
		body = json.dumps(request["body"], ensure_ascii=False, separators=(",", ":")).encode()
		if not any(key.lower() == "content-type" for key in sent):
			sent["content-type"] = "application/json"
	return method, url, sent, body


def urllibTransport(method: str, url: str, headers: dict, body: bytes | None, timeout: float | None) -> tuple[int, str, dict, bytes]:
	"""Send with the standard library, the default transport: `(status, reason, headers, body bytes)`, error statuses
	included. Any transport takes these arguments, `timeout` in seconds. A `Failure` it raises passes through; any other
	error is `Request.Network`, or `Request.Timeout` for a `TimeoutError`, as `fetch` errors are in JavaScript."""
	request = urllib.request.Request(url, data=body, headers=headers, method=method)
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			return response.status, response.reason, dict(response.headers.items()), response.read()
	except urllib.error.HTTPError as error:
		with error:
			return error.code, str(error.reason), dict(error.headers.items()), error.read()


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------


def http(base: str | None = None, *, apis: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None,
	transport: Callable | None = None, timeout: float | None = None) -> dict:
	"""Operations for an environment: `{"RequestSend": ...}`.

	- `base`: the API's URL; `apis` names further APIs, which a request selects with `api`;
	- `headers`: sent with every request (a request's own `headers` override them);
	- `transport`: sends a request, plain (run in a thread) or `async`; the default uses `urllib` (see `urllibTransport`);
	- `timeout`: milliseconds until the request wait times out as `Request.Timeout`, or `None` for no deadline.

	A `Failure` the transport raises passes through; any other error is `Request.Network` (`Request.Timeout` for a timeout).

	Cancelling or timing out cancels an `async` transport; a plain one's thread may continue indefinitely
	if the transport does not honor the timeout passed to it.

	A request is `{method="GET", path, api?, query?, headers?, body?}`, `path` starting with `/` (build it with `p`);
	`body` is sent as JSON. The result is `{status, data}`: `data` is the parsed JSON body, the text of a non-JSON one,
	or `None` when empty.
	"""
	bases = {**(apis or {}), **({"": base} if base is not None else {})}
	if any(not isinstance(value, str) for value in bases.values()):
		raise TypeError("http expects base URLs")
	send = transport or urllibTransport
	defaults = dict(headers or {})

	async def RequestSend(request, context):
		method, url, sent, body = prepare(request, bases, defaults)
		seconds = None if timeout is None else timeout / 1000
		try:
			async with asyncio.timeout(seconds):
				if inspect.iscoroutinefunction(send):
					status, reason, received, content = await send(method, url, sent, body, seconds)
				else:
					status, reason, received, content = await asyncio.to_thread(send, method, url, sent, body, seconds)
		except Failure:
			raise
		except Exception as error:
			cause = error.reason if isinstance(error, urllib.error.URLError) and isinstance(error.reason, BaseException) else error
			if isinstance(cause, TimeoutError):
				raise requestFailure(Request.Timeout, True, method, url, f"no response within {timeout} ms", error)
			raise requestFailure(Request.Network, True, method, url, str(cause), error)
		text = content.decode("utf-8", errors="replace") if content else ""
		if not 200 <= status < 300:
			excerpt = f": {text[:DETAIL_BODY_LENGTH]}" if text else ""
			raise requestFailure(requestCode(status), status in TRANSIENT, method, url, f"{status} {reason}{excerpt}".strip())
		if not text:
			return {"status": status, "data": None}
		if not JSON_RE.search(next((value for key, value in received.items() if key.lower() == "content-type"), "")):
			return {"status": status, "data": text}
		try:
			return {"status": status, "data": json.loads(text)}
		except ValueError as error:
			raise requestFailure(Request.Unreadable, False, method, url, str(error), error)

	return {"RequestSend": RequestSend}

# EOF
