"""Module: test_runtime — Check effects, strands, retry journals and cancellation."""

from __future__ import annotations

import asyncio
import unittest

from hexa.combinators import Policy, always, backoff, collect, lift, parallel, recover, retry, stages
from hexa.runtime import Ledger, Runtime, fork, inert, join, ledger, p, race, report, run
from hexa.shapes import Failure, Gap, codes

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

TestReason = codes("RuntimeTest", "Missing", "Transient")

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------

class RuntimeTests(unittest.IsolatedAsyncioTestCase):
	"""Check runtime behavior independently of pipeline business logic."""

	async def test_plain_yields_and_native_awaitables(self):
		def Plain(env):
			value = yield 3
			return value + 1
		self.assertEqual(await run({}, Plain), 4)
		def Invalid(env):
			yield asyncio.sleep(0)
		with self.assertRaises(Failure) as caught:
			await run({}, Invalid)
		self.assertIs(caught.exception.code, Runtime.NativeAwaitable)

	async def test_plain_dicts_are_values(self):
		for value in ({"instruction": "ledger"}, {"operation": "Read", "arguments": []}):
			self.assertEqual(await run({}, lift(lambda item: item), value), value)

	async def test_ledger_snapshot(self):
		class Pipeline(stages):
			def SnapshotEntry(env):
				yield report(Ledger.Gap, target=Pipeline.SnapshotEntry, reason=TestReason.Missing)
				return (yield ledger())
		self.assertEqual(await run({}, Pipeline.SnapshotEntry), [{"kind": "Gap", "target": "SnapshotEntry", "reason": "RuntimeTest.Missing"}])

	async def test_repeated_join_sequences_and_replay(self):
		def Retrieve(env):
			yield join({"Item": env.Read(1)})
			yield join({"Item": env.Read(2)})
			yield env.Fail()
		env = {"Read": lambda value, context: value, "Fail": lambda context: (_ for _ in ()).throw(Gap(TestReason.Missing)), "record": True}
		with self.assertRaises(Failure) as caught:
			await run(env, Retrieve)
		journal = caught.exception.journal
		self.assertEqual([(entry["strand"], entry["seq"]) for entry in journal], [("/Item", 0), ("/Item", 1), ("", 0)])
		with self.assertRaises(Failure) as replayed:
			await run({"replay": journal}, Retrieve)
		self.assertIs(replayed.exception.code, TestReason.Missing)

	async def test_retry_journal_and_no_wait_replay(self):
		attempts = []
		def read(context):
			attempts.append(context["attempt"])
			if len(attempts) < 3:
				raise OSError("host detail")
			return 42
		def Retrieve(env):
			return (yield env.Read())
		def FailAfter(env, value):
			raise Gap(TestReason.Missing)
			yield
		class Pipeline(stages):
			RetryRetrieve = retry(retry(Retrieve, attempts=3, delay=backoff(0)), attempts=3, delay=backoff(0))
			def RetryEntry(env):
				value = yield from Pipeline.RetryRetrieve(env)
				return (yield from FailAfter(env, value))
		env = {"Read": read, "FailureClassify": lambda error: {"code": TestReason.Transient, "transient": True}, "record": True, "policy": {"retry": {"default": True, "attempts": 3}}}
		with self.assertRaises(Failure) as caught:
			await run(env, Pipeline.RetryEntry)
		self.assertEqual(attempts, [1, 2, 3])
		journal = caught.exception.journal
		self.assertEqual([entry["seq"] for entry in journal], list(range(5)))
		self.assertEqual([entry["operation"] for entry in journal], ["Read", "DelayWait", "Read", "DelayWait", "Read"])
		with self.assertRaises(Failure) as caught:
			await run({"replay": journal, "policy": env["policy"]}, Pipeline.RetryEntry)
		self.assertIs(caught.exception.code, TestReason.Missing)
		self.assertEqual(attempts, [1, 2, 3])

	async def test_retry_exhaustion(self):
		calls = []
		def read(context):
			calls.append(1)
			raise OSError()
		def Retrieve(env):
			return (yield env.Read())
		wrapped = retry(retry(Retrieve, attempts=2, delay=backoff(0)), attempts=3, delay=backoff(0))
		with self.assertRaises(Failure) as caught:
			await run({"Read": read, "FailureClassify": lambda error: {"code": TestReason.Transient, "transient": True}}, wrapped)
		self.assertEqual(len(calls), 2)
		self.assertTrue(caught.exception.exhausted)

	async def test_parallel_order_and_limit(self):
		active, peak = 0, 0
		async def read(value, context):
			nonlocal active, peak
			active += 1
			peak = max(peak, active)
			await asyncio.sleep((4 - value) * 0.001)
			active -= 1
			return value
		class Pipeline(stages):
			def ParallelItem(env, value):
				yield report(Ledger.Gap, target=Pipeline.ParallelItem, reason=TestReason.Missing, path=[value])
				return (yield env.Read(value))
			ParallelEntry = collect(parallel(ParallelItem))
		result = await run({"Read": read, "policy": {"maxConcurrency": 2}}, Pipeline.ParallelEntry, [0, 1, 2, 3])
		self.assertEqual(result["value"], [0, 1, 2, 3])
		self.assertEqual([entry["path"] for entry in result["ledger"]], [[0], [1], [2], [3]])
		self.assertEqual(peak, 2)

	async def test_fork_join_and_unjoined(self):
		def Child(env, value):
			return (yield value + 1)
		def Parent(env):
			handle = yield fork(Child, 3)
			return (yield join([handle]))
		self.assertEqual(await run({}, Parent), [4])
		def Unjoined(env):
			yield fork(Child, 3)
		with self.assertRaises(Failure) as caught:
			await run({}, Unjoined)
		self.assertIs(caught.exception.code, Runtime.StrandUnjoined)

	async def test_race_cancels_loser_and_replays(self):
		cancelled = asyncio.Event()
		async def read(value, context):
			if value == "slow":
				try:
					await asyncio.Event().wait()
				finally:
					cancelled.set()
			return value
		def Racing(env):
			return (yield race({"Slow": env.Read("slow"), "Fast": env.Read("fast")}))
		self.assertEqual(await run({"Read": read}, Racing), {"Fast": "fast"})
		self.assertTrue(cancelled.is_set())
		def Recorded(env):
			yield from Racing(env)
			raise Gap(TestReason.Missing)
		with self.assertRaises(Failure) as caught:
			await run({"Read": read, "record": True}, Recorded)
		journal = caught.exception.journal
		self.assertTrue(any(entry.get("cancelled") for entry in journal))
		self.assertEqual(await run({"replay": journal}, Racing), {"Fast": "fast"})

	async def test_race_uses_actual_completion_order(self):
		ready, gates = asyncio.Event(), [asyncio.Event(), asyncio.Event()]
		started = []
		async def read(value, context):
			started.append(value)
			if len(started) == 2:
				ready.set()
			await gates[value].wait()
			return value
		def Racing(env):
			return (yield race([env.Read(0), env.Read(1)]))
		task = asyncio.create_task(run({"Read": read}, Racing))
		await ready.wait()
		gates[1].set()
		gates[0].set()
		self.assertEqual(await task, 1)

	async def test_completed_race_records_and_replays_winner(self):
		ready, gates = asyncio.Event(), [asyncio.Event(), asyncio.Event()]
		started = []
		async def read(value, context):
			started.append(value)
			if len(started) == 2:
				ready.set()
			await gates[value].wait()
			return value
		def Racing(env):
			return (yield race([env.Read(0), env.Read(1)]))
		def Recorded(env):
			yield from Racing(env)
			raise Gap(TestReason.Missing)
		task = asyncio.create_task(run({"Read": read, "record": True}, Recorded))
		await ready.wait()
		gates[1].set()
		gates[0].set()
		with self.assertRaises(Failure) as caught:
			await task
		journal = caught.exception.journal
		effects = [entry for entry in journal if "operation" in entry]
		decisions = [entry for entry in journal if "instruction" in entry]
		self.assertEqual(len(effects), 2)
		self.assertTrue(all("result" in entry and entry["seq"] == 0 for entry in effects))
		self.assertEqual(decisions, [{"instruction": "race", "strand": "", "ordinal": 0, "keys": [0, 1], "winner": 1, "index": 2}])
		def forbidden(*arguments):
			self.fail("race replay called a host operation")
		# Completion indexes and journal array order never select the replay winner.
		for entry in effects:
			entry["index"] = 99
		self.assertEqual(await run({"replay": list(reversed(journal)), "Read": forbidden}, Racing), 1)

	async def test_legacy_race_without_decision_fails_closed(self):
		def Racing(env):
			return (yield race([env.Read(0), env.Read(1)]))
		journal = [{"strand": f"/{index}", "seq": 0, "operation": "Read", "arguments": [index], "result": index, "attempt": 1, "index": index} for index in range(2)]
		with self.assertRaises(Failure) as caught:
			await run({"replay": journal}, Racing)
		self.assertIs(caught.exception.code, Runtime.ReplayMismatch)

	async def test_effect_free_race_still_records_a_decision(self):
		def Pure(env, value):
			return (yield value)
		def Racing(env):
			first = yield fork(Pure, 0)
			second = yield fork(Pure, 1)
			return (yield race([first, second]))
		def Recorded(env):
			yield from Racing(env)
			raise Gap(TestReason.Missing)
		with self.assertRaises(Failure) as caught:
			await run({"record": True}, Recorded)
		journal = caught.exception.journal
		self.assertEqual(journal, [{"instruction": "race", "strand": "", "ordinal": 0, "keys": [0, 1], "winner": 0, "index": 0}])
		self.assertEqual(await run({"replay": journal}, Racing), 0)

	async def test_nested_race_and_join_replay(self):
		def Racing(env):
			return (yield race({"Nested": race([env.Read(0), env.Read(1)]), "Other": env.Read(2)}))
		journal = [
			{"instruction": "race", "strand": "", "ordinal": 0, "keys": ["Nested", "Other"], "winner": "Nested"},
			{"instruction": "race", "strand": "/Nested", "ordinal": 0, "keys": [0, 1], "winner": 1},
			*({"strand": strand, "seq": 0, "operation": "Read", "arguments": [value], "result": value, "attempt": 1} for strand, value in (("/Nested/0", 0), ("/Nested/1", 1), ("/Other", 2))),
		]
		self.assertEqual(await run({"replay": journal}, Racing), {"Nested": 1})
		def Joining(env):
			return (yield race({"Nested": join([env.Read(0), env.Read(1)]), "Other": env.Read(2)}))
		journal = [entry for entry in journal if entry.get("instruction") != "race" or entry["strand"] == ""]
		self.assertEqual(await run({"replay": journal}, Joining), {"Nested": [0, 1]})

	async def test_race_handles_preserve_loser_work_and_ledger_order(self):
		class Pipeline(stages):
			def RaceLedgerChild(env, value):
				result = yield env.Read(value)
				yield report(Ledger.Gap, target=Pipeline.RaceLedgerChild, reason=TestReason.Missing, path=[value])
				return result
			def RaceLedgerParent(env):
				first = yield fork(Pipeline.RaceLedgerChild, 0)
				second = yield fork(Pipeline.RaceLedgerChild, 1)
				return (yield race({"First": first, "Second": second}))
			RaceLedgerEntry = collect(RaceLedgerParent)
		journal = [
			{"instruction": "race", "strand": "", "ordinal": 0, "keys": ["First", "Second"], "winner": "Second"},
			*({"strand": f"/@{value}", "seq": 0, "operation": "Read", "arguments": [value], "result": value, "attempt": 1} for value in range(2)),
		]
		result = await run({"replay": journal}, Pipeline.RaceLedgerEntry)
		self.assertEqual(result["value"], {"Second": 1})
		self.assertEqual([entry["path"] for entry in result["ledger"]], [[0], [1]])

	async def test_race_loser_nested_join_preserves_recorded_work(self):
		ready = asyncio.Event()
		async def read(value, context):
			if value == "stop":
				await asyncio.Event().wait()
			if value == "winner":
				await ready.wait()
			return value
		def done(context):
			ready.set()
		class Pipeline(stages):
			def RaceDeepWork(env):
				value = yield join([join([join([env.Read("work")])])])
				yield report(Ledger.Gap, target=Pipeline.RaceDeepWork, reason=TestReason.Missing, path=["deep"])
				yield env.WorkDone()
				return value
			def RaceDeepLoser(env):
				handle = yield fork(Pipeline.RaceDeepWork)
				return (yield join({"Stop": env.Read("stop"), "Deep": handle}))
			def RaceDeepParent(env):
				handle = yield fork(Pipeline.RaceDeepLoser)
				return (yield race({"Loser": handle, "Winner": env.Read("winner")}))
			RaceDeepEntry = collect(RaceDeepParent)
		def Recorded(env):
			yield from Pipeline.RaceDeepEntry(env)
			raise Gap(TestReason.Missing)
		with self.assertRaises(Failure) as caught:
			await run({"Read": read, "WorkDone": done, "record": True}, Recorded)
		self.assertIs(caught.exception.code, TestReason.Missing)
		result = await run({"replay": caught.exception.journal}, Pipeline.RaceDeepEntry)
		self.assertEqual(result["value"], {"Winner": "winner"})
		self.assertEqual(result["ledger"], caught.exception.ledger)
		self.assertEqual(result["ledger"][0]["path"], ["deep"])

	async def test_repeated_race_ordinals_do_not_consume_effect_sequences(self):
		def Racing(env):
			yield env.Read("before")
			first = yield race({"A": env.Read(1), "B": env.Read(2)})
			second = yield race({"A": env.Read(3), "B": env.Read(4)})
			return [first, second, (yield env.Read("after"))]
		def Recorded(env):
			yield from Racing(env)
			raise Gap(TestReason.Missing)
		with self.assertRaises(Failure) as caught:
			await run({"Read": lambda value, context: value, "record": True}, Recorded)
		journal = caught.exception.journal
		decisions = [entry for entry in journal if entry.get("instruction") == "race"]
		self.assertEqual([entry["ordinal"] for entry in decisions], [0, 1])
		self.assertTrue(all("seq" not in entry for entry in decisions))
		for strand in ("", "/A", "/B"):
			self.assertEqual([entry["seq"] for entry in journal if "operation" in entry and entry["strand"] == strand], [0, 1])
		self.assertEqual(await run({"replay": journal}, Racing), [{"A": 1}, {"A": 3}, "after"])

	async def test_retried_race_advances_ordinal(self):
		def read(value, context):
			if value == "a" and context["attempt"] == 1:
				raise OSError()
			return value
		def Racing(env):
			return (yield race({"A": env.Read("a"), "B": env.Read("b")}))
		wrapped = retry(Racing, attempts=2, delay=backoff(0))
		def Recorded(env):
			yield from wrapped(env)
			raise Gap(TestReason.Missing)
		env = {"Read": read, "record": True, "FailureClassify": lambda error: {"code": TestReason.Transient, "transient": True}}
		with self.assertRaises(Failure) as caught:
			await run(env, Recorded)
		self.assertIs(caught.exception.code, TestReason.Missing)
		journal = caught.exception.journal
		self.assertEqual([entry["ordinal"] for entry in journal if entry.get("instruction") == "race"], [0, 1])
		self.assertEqual([entry["seq"] for entry in journal if entry.get("operation") == "DelayWait"], [0])
		self.assertEqual(await run({"replay": journal}, wrapped), {"A": "a"})

	async def test_race_failure_is_a_completion_not_a_fallback(self):
		def read(value, context):
			if value == "bad":
				raise Gap(TestReason.Missing)
			return value
		def Racing(env):
			return (yield race({"Bad": env.Read("bad"), "Good": env.Read("good")}))
		with self.assertRaises(Failure) as caught:
			await run({"Read": read, "record": True}, Racing)
		self.assertIs(caught.exception.code, TestReason.Missing)
		journal = caught.exception.journal
		self.assertEqual(next(entry for entry in journal if entry.get("instruction") == "race")["winner"], "Bad")
		with self.assertRaises(Failure) as replayed:
			await run({"replay": journal}, Racing)
		self.assertIs(replayed.exception.code, TestReason.Missing)

	async def test_cancelled_race_records_and_replays_cancellation(self):
		ready, signal = asyncio.Event(), asyncio.Event()
		started = []
		async def read(value, context):
			started.append(value)
			if len(started) == 2:
				ready.set()
			await asyncio.Event().wait()
		def Racing(env):
			return (yield race([env.Read(0), env.Read(1)]))
		task = asyncio.create_task(run({"Read": read, "record": True, "signal": signal}, Racing))
		await ready.wait()
		signal.set()
		with self.assertRaises(Failure) as caught:
			await task
		self.assertIs(caught.exception.kind, Failure.Cancel)
		journal = caught.exception.journal
		decision = next(entry for entry in journal if entry.get("instruction") == "race")
		self.assertEqual({key: value for key, value in decision.items() if key != "index"}, {"instruction": "race", "strand": "", "ordinal": 0, "keys": [0, 1], "cancelled": True})
		with self.assertRaises(Failure) as replayed:
			await run({"replay": journal}, Racing)
		self.assertIs(replayed.exception.kind, Failure.Cancel)
		# The race-level cancellation remains authoritative even if both effects succeeded.
		completed = [{**entry, "result": entry["arguments"][0]} if "operation" in entry else entry for entry in journal]
		for entry in completed:
			if "operation" in entry:
				del entry["cancelled"]
		with self.assertRaises(Failure) as replayed:
			await run({"replay": completed}, Racing)
		self.assertIs(replayed.exception.kind, Failure.Cancel)

	async def test_selected_cancelled_participant_does_not_fall_back(self):
		def read(value, context):
			if value == 0:
				raise asyncio.CancelledError
			return value
		def Racing(env):
			return (yield race([env.Read(0), env.Read(1)]))
		with self.assertRaises(Failure) as caught:
			await run({"Read": read, "record": True}, Racing)
		self.assertIs(caught.exception.kind, Failure.Cancel)
		journal = caught.exception.journal
		decision = next(entry for entry in journal if entry.get("instruction") == "race")
		self.assertEqual(decision["winner"], 0)
		self.assertNotIn("cancelled", decision)
		with self.assertRaises(Failure) as replayed:
			await run({"replay": journal}, Racing)
		self.assertIs(replayed.exception.kind, Failure.Cancel)

	async def test_race_replay_does_not_ignore_loser_mismatch(self):
		def Racing(env):
			return (yield race([env.Read(0), env.Read(1)]))
		journal = [
			{"instruction": "race", "strand": "", "ordinal": 0, "keys": [0, 1], "winner": 1},
			{"strand": "/0", "seq": 0, "operation": "Read", "arguments": [999], "result": 0},
			{"strand": "/1", "seq": 0, "operation": "Read", "arguments": [1], "result": 1},
		]
		with self.assertRaises(Failure) as caught:
			await run({"replay": journal}, Racing)
		self.assertIs(caught.exception.code, Runtime.ReplayMismatch)

	async def test_race_decision_validation(self):
		def Racing(env):
			return (yield race([env.Read(0), env.Read(1)]))
		decision = {"instruction": "race", "strand": "", "ordinal": 0, "keys": [0, 1], "winner": 1}
		malformed = [
			{**decision, "ordinal": -1}, {**decision, "ordinal": True},
			{**decision, "keys": []}, {**decision, "keys": [0, 0]},
			{**decision, "keys": ["A", 1]}, {**decision, "keys": ["@0", "A"]},
			{**decision, "winner": True}, {**decision, "winner": 2},
			{**decision, "winner": "1"}, {**decision, "cancelled": True},
			{**decision, "instruction": "join"}, {**decision, "seq": 0},
			{key: value for key, value in decision.items() if key != "winner"},
			{"instruction": "race", "strand": "", "ordinal": 0, "keys": [0, 1], "cancelled": False},
		]
		for entry in malformed:
			with self.subTest(entry=entry), self.assertRaises(Failure) as caught:
				await run({"replay": [entry]}, Racing)
			self.assertIs(caught.exception.kind, Failure.Contract)
			self.assertIs(caught.exception.code, Runtime.ReplayMismatch)
		for entries in ([decision, decision], [{**decision, "keys": ["A", "B"], "winner": "A"}]):
			with self.subTest(entries=entries), self.assertRaises(Failure) as caught:
				await run({"replay": entries}, Racing)
			self.assertIs(caught.exception.code, Runtime.ReplayMismatch)
		journal = [decision, {**decision, "ordinal": 1}, *({"strand": f"/{value}", "seq": 0, "operation": "Read", "arguments": [value], "result": value} for value in range(2))]
		with self.assertRaises(Failure) as caught:
			await run({"replay": journal}, Racing)
		self.assertIs(caught.exception.code, Runtime.ReplayMismatch)

	async def test_sibling_failure_cancels_work(self):
		cancelled = asyncio.Event()
		async def read(value, context):
			if value == "bad":
				raise OSError()
			try:
				await asyncio.Event().wait()
			finally:
				cancelled.set()
		def Retrieve(env):
			return (yield join([env.Read("slow"), env.Read("bad")]))
		with self.assertRaises(Failure) as caught:
			await run({"Read": read}, Retrieve)
		self.assertIs(caught.exception.kind, Failure.Effect)
		self.assertTrue(cancelled.is_set())

	async def test_cancel_closes_without_recovery_or_always(self):
		closed, cleanup = [], []
		started, signal = asyncio.Event(), asyncio.Event()
		async def read(context):
			started.set()
			await asyncio.Event().wait()
		def Waiting(env):
			try:
				try:
					yield env.Read()
				except Failure:
					self.fail("cancellation was thrown into the stage")
			finally:
				closed.append(True)
		wrapped = always(recover(Waiting, Policy.Substitute(1), on=[Failure.Cancel]), lift(lambda state: cleanup.append(state)))
		task = asyncio.create_task(run({"Read": read, "signal": signal}, wrapped))
		await started.wait()
		signal.set()
		with self.assertRaises(Failure) as caught:
			await task
		self.assertIs(caught.exception.kind, Failure.Cancel)
		self.assertEqual(closed, [True])
		self.assertEqual(cleanup, [])

	async def test_timeout_is_cancel(self):
		def read(context):
			raise TimeoutError()
		def Waiting(env):
			yield env.Read()
		with self.assertRaises(Failure) as caught:
			await run({"Read": read}, recover(Waiting, Policy.Substitute(1), on=[Failure.Cancel]))
		self.assertIs(caught.exception.kind, Failure.Cancel)

	async def test_close_yielded(self):
		started, signal = asyncio.Event(), asyncio.Event()
		async def read(context):
			started.set()
			await asyncio.Event().wait()
		def Invalid(env):
			try:
				yield env.Read()
			finally:
				yield 1
		task = asyncio.create_task(run({"Read": read, "signal": signal}, Invalid))
		await started.wait()
		signal.set()
		with self.assertRaises(Failure) as caught:
			await task
		self.assertIs(caught.exception.code, Runtime.CloseYielded)

	async def test_replay_mismatch(self):
		def Read(env):
			return (yield env.Read(1))
		entry = {"strand": "", "seq": 0, "operation": "Read", "arguments": [2], "result": 3, "attempt": 1}
		with self.assertRaises(Failure) as caught:
			await run({"replay": [entry]}, Read)
		self.assertIs(caught.exception.code, Runtime.ReplayMismatch)

	async def test_snapshots_and_boundary_data(self):
		environment = {"root": {"list": [1]}, "policy": {"maxGaps": 5}}
		frozen = inert(environment)
		environment["root"]["list"].append(2)
		self.assertEqual(frozen.root["list"], (1,))
		with self.assertRaises(TypeError):
			frozen.root["bad"] = 1
		with self.assertRaises(AttributeError):
			frozen.root = {}
		for value in (float("nan"), {"__proto__": {}}, {1: "invalid"}):
			with self.assertRaises(Failure):
				await run({}, lift(lambda value: value), value)

	async def test_holey_maps_cross_effect_boundary(self):
		def Publish(env):
			return (yield env.Publish({0: 10, 2: 30}))
		self.assertEqual(await run({"Publish": lambda value, context: value}, Publish), {"0": 10, "2": 30})

	async def test_success_does_not_signal_cancellation(self):
		contexts = []
		def read(context):
			contexts.append(context)
			return 1
		def Retrieve(env):
			return (yield env.Read())
		self.assertEqual(await run({"Read": read}, Retrieve), 1)
		self.assertFalse(contexts[0]["signal"].is_set())

	async def test_unretried_effect_can_be_recovered(self):
		def read(context):
			raise OSError()
		def Retrieve(env, value):
			return (yield env.Read())
		env = {"Read": read, "FailureClassify": lambda error: {"code": TestReason.Transient, "transient": True}}
		self.assertEqual(await run(env, recover(Retrieve, Policy.Substitute(42), on=[Failure.Effect]), 1), 42)

	def test_paths(self):
		identifier = "a/b ?#%"
		self.assertEqual(p(t"/items/{identifier}"), "/items/a%2Fb%20%3F%23%25")
		for identifier in ("", ".", "..", True, None, [], 2**53):
			with self.assertRaises(Failure) as caught:
				p(t"/items/{identifier}")
			self.assertIs(caught.exception.code, Runtime.PathSegmentInvalid)
		with self.assertRaises(TypeError):
			p("/items/1")
		with self.assertRaises(Failure):
			p(t"/items/{1!s}")

# EOF
