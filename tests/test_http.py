"""Module: test_http — `RequestSend` over HTTP, journals of successful runs and failure details."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from hexa.combinators import backoff
from hexa.http import Request, http
from hexa.runtime import Runtime, p, run
from hexa.shapes import Failure

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
	"""The API of the JavaScript test (`tests/js/http.test.js`)."""

	def log_message(self, *args):
		pass

	def answer(self, status, body=b"", kind="application/json", reason=None):
		self.send_response(status, reason)
		if body:
			self.send_header("content-type", kind)
		self.send_header("content-length", str(len(body)))
		self.end_headers()
		self.wfile.write(body)

	def do_GET(self):
		url = urlsplit(self.path)
		if url.path == "/v1/clients/7":
			self.answer(200, json.dumps({"id": 7, "query": "?" + url.query if url.query else "", "auth": self.headers.get("authorization")}).encode())
		elif url.path == "/v1/text":
			self.answer(200, b"plain", "text/plain")
		elif url.path == "/v1/empty":
			self.answer(204)
		elif url.path == "/v1/broken":
			self.answer(200, b"{")
		else:
			self.answer(404, b"no such client", "text/plain", "Not Found")

	def do_POST(self):
		body = json.loads(self.rfile.read(int(self.headers["content-length"])))
		self.answer(200, json.dumps({"method": "POST", "type": self.headers.get("content-type"), "body": body}).encode())


def Send(env, request):
	return (yield env.RequestSend(request))


# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------


class HttpTests(unittest.IsolatedAsyncioTestCase):
	"""Check the HTTP operation as the JavaScript tests do."""

	@classmethod
	def setUpClass(cls):
		cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		threading.Thread(target=cls.server.serve_forever, daemon=True).start()
		cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1/"

	@classmethod
	def tearDownClass(cls):
		cls.server.shutdown()
		cls.server.server_close()

	async def test_a_request_reaches_the_api_and_returns_the_status_and_parsed_data(self):
		env = http(self.base, headers={"authorization": "Bearer token"})
		self.assertEqual(await run(env, Send, {"path": p(t"/clients/{7}"), "query": {"expand": ["notes", "reviews"], "active": True}}), {
			"status": 200, "data": {"id": 7, "query": "?expand=notes&expand=reviews&active=true", "auth": "Bearer token"}})
		self.assertEqual(await run(env, Send, {"method": "POST", "path": "/echo", "body": {"name": "Aroha"}}), {
			"status": 200, "data": {"method": "POST", "type": "application/json", "body": {"name": "Aroha"}}})
		self.assertEqual(await run(env, Send, {"path": "/text"}), {"status": 200, "data": "plain"})
		self.assertEqual(await run(env, Send, {"path": "/empty"}), {"status": 204, "data": None})
		self.assertEqual((await run(http(apis={"crm": self.base}), Send, {"api": "crm", "path": "/clients/7"}))["status"], 200)
		self.assertEqual((await run(env, Send, {"api": None, "path": "/clients/7"}))["status"], 200)

	async def test_an_error_status_is_a_coded_effect_failure_whose_message_the_journal_keeps(self):
		journal = []
		with self.assertRaises(Failure) as caught:
			await run({**http(self.base), "record": journal.append}, Send, {"path": "/clients/8"})
		failure = caught.exception
		self.assertEqual((failure.kind, failure.code, failure.transient), (Failure.Effect, Request.NotFound, False))
		self.assertEqual(len(journal), 1)
		self.assertEqual(journal[0]["failure"]["detail"], f"GET {self.base}clients/8: 404 Not Found: no such client")
		with self.assertRaises(Failure) as caught:
			await run({"replay": journal}, Send, {"path": "/clients/8"})
		self.assertEqual((caught.exception.code, str(caught.exception.__cause__)), (Request.NotFound, journal[0]["failure"]["detail"]))

	async def test_a_successful_runs_journal_reaches_the_record_sink(self):
		journal = []
		value = await run({**http(self.base), "record": journal.append}, Send, {"path": "/clients/7"})
		self.assertEqual([(entry["index"], entry["operation"], entry["result"]) for entry in journal], [(0, "RequestSend", value)])
		self.assertEqual(await run({"replay": journal}, Send, {"path": "/clients/7"}), value)
		self.assertEqual(await run({**http(self.base), "record": None}, Send, {"path": "/clients/7"}), value)

	async def test_a_failing_record_sink_fails_the_run_at_its_end_without_hiding_its_outcome(self):
		calls = []
		def broken(entry):
			calls.append(entry)
			raise RuntimeError("audit log down")
		with self.assertRaises(Failure) as caught:
			await run({**http(self.base), "record": broken}, Send, {"path": "/clients/7"})
		self.assertEqual((caught.exception.kind, caught.exception.code, str(caught.exception.__cause__)), (Failure.Defect, Runtime.RecordFailed, "audit log down"))
		self.assertEqual((len(calls), len(caught.exception.journal)), (1, 1))
		with self.assertRaises(Failure) as caught:
			await run({**http(self.base), "record": broken}, Send, {"path": "/clients/8"})
		self.assertIs(caught.exception.code, Request.NotFound)

	async def test_transient_statuses_and_network_failures_are_retried(self):
		answers = [(503, "Service Unavailable", {}, b"busy"), (200, "OK", {"Content-Type": "application/json"}, b'{"ok": true}')]
		journal = []
		env = {**http(self.base, transport=lambda *request: answers.pop(0)), "record": journal.append,
			"policy": {"retry": {"default": True, "attempts": 2, "delay": backoff(0)}}}
		self.assertEqual(await run(env, Send, {"path": "/clients/7"}), {"status": 200, "data": {"ok": True}})
		self.assertEqual([(entry["operation"], entry.get("failure", {}).get("code"), entry.get("failure", {}).get("transient")) for entry in journal], [
			("RequestSend", "Request.Unavailable", True), ("DelayWait", None, None), ("RequestSend", None, None)])

		def refused(*request):
			raise ConnectionRefusedError("connection refused")
		with self.assertRaises(Failure) as caught:
			await run(http(self.base, transport=refused), Send, {"path": "/clients/7"})
		self.assertEqual((caught.exception.code, caught.exception.transient, str(caught.exception.__cause__)),
			(Request.Network, True, f"GET {self.base}clients/7: connection refused"))

		# Any transport error is a network failure, as `fetch` errors are; a transport's own `Failure` passes through.
		def unknown(*request):
			raise ValueError("protocol error")
		def refusing(*request):
			raise Failure(Failure.Effect, Request.Forbidden)
		for transport, code in ((unknown, Request.Network), (refusing, Request.Forbidden)):
			with self.subTest(code=code), self.assertRaises(Failure) as caught:
				await run(http(self.base, transport=transport), Send, {"path": "/clients/7"})
			self.assertIs(caught.exception.code, code)

	async def test_a_timeout_is_a_transient_effect_failure(self):
		async def hang(*request):
			raise TimeoutError()
		with self.assertRaises(Failure) as caught:
			await run(http(self.base, transport=hang, timeout=5), Send, {"path": "/clients/7"})
		self.assertEqual((caught.exception.code, caught.exception.transient), (Request.Timeout, True))

		# A transport that ignores its `timeout`, plain or async, still fails in time.
		async def ignore(*request):
			await asyncio.sleep(10)
		for slow in (ignore, lambda *request: time.sleep(0.2)):
			with self.subTest(transport=slow), self.assertRaises(Failure) as caught:
				await run(http(self.base, transport=slow, timeout=20), Send, {"path": "/clients/7"})
			self.assertEqual((caught.exception.code, caught.exception.transient), (Request.Timeout, True))

		async def wait(*request):
			await asyncio.sleep(10)
		signal = asyncio.Event()
		pending = asyncio.create_task(run({**http(self.base, transport=wait), "signal": signal}, Send, {"path": "/clients/7"}))
		await asyncio.sleep(0.01)
		signal.set()
		with self.assertRaises(Failure) as caught:
			await pending
		self.assertIs(caught.exception.kind, Failure.Cancel)

	async def test_malformed_requests_and_unreadable_json_fail(self):
		for request in ({"path": "clients/7"}, {"method": "TRACE", "path": "/clients/7"}, {"api": "other", "path": "/clients/7"}, {"path": "/x", "query": {"a": {"b": 1}}}):
			with self.subTest(request=request), self.assertRaises(Failure) as caught:
				await run(http(self.base), Send, request)
			self.assertIs(caught.exception.kind, Failure.Contract)
		with self.assertRaises(Failure) as caught:
			await run(http(self.base), Send, {"path": "/broken"})
		self.assertEqual((caught.exception.kind, caught.exception.code, caught.exception.transient), (Failure.Effect, Request.Unreadable, False))


if __name__ == "__main__":
	unittest.main()

# EOF
