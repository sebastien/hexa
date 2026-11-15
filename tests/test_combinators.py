"""Module: test_combinators — Check declarations, contracts and policy composition."""

from __future__ import annotations

import unittest

from hexa.combinators import (
	Policy, admit, always, bound, choose, collect, each, fold, lift,
	guard, otherwise, pipe, project, recover, stage, stages, tap, when,
)
from hexa.runtime import Checks, Ledger, Runtime, report, run
from hexa.shapes import Failure, Gap, Int, Text, Tuple, codes, decode, either, encode, slots, tolerant

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

TestReason = codes("CombinatorTest", "Missing", "Cleanup", "Rejected")
TestSlots = slots("CombinatorTestSlots", Id=Int, Name=Text)


def Missing(env, value):
	"""Fail with a declared recoverable absence."""
	raise Gap(TestReason.Missing, ["missing"])
	yield

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------

class CombinatorTests(unittest.IsolatedAsyncioTestCase):
	"""Assert on members and structured fields rather than message text."""

	async def test_stage_decorator_preserves_name_and_contracts(self):
		import hexa.stage
		self.assertIs(hexa.stage.stage, stage)
		@stage("PyNamedDecorator", input=Tuple(Int), output=Int)
		def PyNamedDecorator(env, value):
			return (yield value)
		self.assertEqual(PyNamedDecorator.name, "PyNamedDecorator")
		self.assertEqual(encode(PyNamedDecorator), "$PyNamedDecorator")
		self.assertIs(decode("$PyNamedDecorator"), PyNamedDecorator)
		self.assertEqual(await run({}, PyNamedDecorator, 1), 1)
		with self.assertRaises(Failure) as caught:
			await run({}, PyNamedDecorator, "bad")
		self.assertIs(caught.exception.kind, Failure.Contract)
		with self.assertRaises(Failure) as caught:
			stage("PyRenamedDecorator")(PyNamedDecorator)
		self.assertIs(caught.exception.code, Runtime.StageRenamed)
		with self.assertRaises(Failure) as caught:
			stage("PyDifferentGenerator")(Missing)
		self.assertIs(caught.exception.code, Runtime.StageRenamed)

	async def test_guard_inferred_output_limits_substitutes(self):
		fixed = guard(Int, TestReason.Missing)
		self.assertEqual(await run({}, fixed, 1), 1)
		self.assertEqual(await run({}, recover(pipe(Missing, fixed), Policy.Substitute(1)), "bad"), 1)
		with self.assertRaises(Failure) as caught:
			await run({}, recover(pipe(Missing, fixed), Policy.Substitute("bad")), "bad")
		self.assertIs(caught.exception.kind, Failure.Defect)
		self.assertEqual(await run({}, recover(pipe(Missing, guard(lambda env: Int, TestReason.Missing)), Policy.Substitute("dynamic")), "bad"), "dynamic")

	async def test_naming(self):
		class Pipeline(stages):
			NamingIncrement = lift(lambda value: value + 1)
			NamingEntry = pipe(NamingIncrement, NamingIncrement)
		self.assertEqual(await run({}, Pipeline.NamingEntry, 1), 3)
		with self.assertRaises(AttributeError) as caught:
			Pipeline.Unknown
		self.assertIs(caught.exception.code, Runtime.MemberUnknown)
		with self.assertRaises(AttributeError):
			Pipeline.Other = Pipeline.NamingEntry
		with self.assertRaises(AttributeError):
			del Pipeline.NamingEntry
		with self.assertRaises(AttributeError):
			Pipeline.NamingEntry.name = "Other"
		with self.assertRaises(TypeError):
			Pipeline()
		with self.assertRaises(Failure) as caught:
			class Renamed(stages):
				Other = Pipeline.NamingEntry
		self.assertIs(caught.exception.code, Runtime.StageRenamed)
		with self.assertRaises(TypeError):
			class Invalid(stages):
				Data = 3

	async def test_registry(self):
		def Text(env, value):
			return (yield value)
		pipe(Text)
		self.assertEqual(encode(decode("$Text")), "$Text")
		with self.assertRaises(Failure) as caught:
			codes("Text", "Other")
		self.assertIs(caught.exception.code, Runtime.NameRedeclared)
		self.assertIs(codes("CombinatorTest", "Missing", "Cleanup", "Rejected"), TestReason)
		class First(stages):
			def RegistryDuplicate(env):
				yield
		with self.assertRaises(Failure) as caught:
			class Second(stages):
				def RegistryDuplicate(env):
					yield
		self.assertIs(caught.exception.code, Runtime.NameRedeclared)

	async def test_policies(self):
		with self.assertRaises(Failure) as caught:
			codes("Policy", "Shadow")
		self.assertIs(caught.exception.code, Runtime.NameRedeclared)
		self.assertEqual(await run({}, recover(Missing, Policy.Substitute(42)), 1), 42)
		self.assertEqual(await run({}, recover(Missing, Policy.Fallback(lift(lambda value: value + 1))), 1), 2)
		self.assertEqual(await run({}, each(Missing, Policy.Drop), [1, 2]), [])
		with self.assertRaises(TypeError):
			recover(Missing, Policy.Drop)
		with self.assertRaises(Failure) as caught:
			await run({}, recover(recover(Missing, Policy.Abort), Policy.Substitute(42)), 1)
		self.assertIs(caught.exception.code, TestReason.Missing)
		self.assertTrue(caught.exception.fatal)
		self.assertEqual(len(caught.exception.ledger), 1)
		class Pipeline(stages):
			PolicyEncoded = lift(lambda value: value)
		self.assertEqual(encode(Pipeline.PolicyEncoded), "$PolicyEncoded")
		self.assertIs(decode("$PolicyEncoded"), Pipeline.PolicyEncoded)
		with self.assertRaises(TypeError):
			encode(lift(lambda value: value))
		for policy in (Policy.Abort, Policy.Drop, Policy.Substitute({"literal": "$Text"}), Policy.Fallback(stage("CombinatorFallback", lift(lambda value: value)))):
			self.assertEqual(encode(decode(encode(policy))), encode(policy))
		effect = {"operation": "Read", "arguments": [{"literal": "$Text"}]}
		self.assertEqual(decode(encode(effect)), effect)

	async def test_unrecoverable_kinds(self):
		for kind in (Failure.Contract, Failure.Budget, Failure.Defect):
			def Fail(env, value):
				raise Failure(kind, TestReason.Missing)
				yield
			with self.subTest(kind=kind), self.assertRaises(Failure) as caught:
				await run({}, recover(Fail, Policy.Substitute(42), on=[kind]), 1)
			self.assertIs(caught.exception.kind, kind)
		def Effect(env, value):
			raise Failure(Failure.Effect, TestReason.Missing)
			yield
		with self.assertRaises(Failure):
			await run({}, recover(Effect, Policy.Substitute(42)), 1)
		self.assertEqual(await run({}, recover(Effect, Policy.Substitute(42), on=[Failure.Effect]), 1), 42)

	async def test_holes_and_item_policy(self):
		class Pipeline(stages):
			HolesRowsProject = each(project({"name": tolerant(TestSlots.Name)}, {"Name": ~TestSlots.Name}), Policy.Drop)
			HolesEntry = collect(HolesRowsProject)
		result = await run({}, Pipeline.HolesEntry, [{"name": 1}, {"name": "ok"}])
		self.assertEqual(result["value"], [{}, {"Name": "ok"}])
		self.assertEqual(result["ledger"][0]["path"], ["Name"])
		self.assertEqual(result["ledger"][0]["target"], "HolesRowsProject")
		strict = each(project({"name?": TestSlots.Name}, {"Name": TestSlots.Name}, holes=Policy.Abort), Policy.Drop)
		self.assertEqual(await run({}, strict, [{}]), [])
		with self.assertRaises(TypeError):
			project({"name": TestSlots.Name}, {"Id": TestSlots.Id})
		with self.assertRaises(TypeError):
			project(either({"name": TestSlots.Name}, {"id": TestSlots.Id}), {"Name": TestSlots.Name})
		project(either({"name": TestSlots.Name}, {"id": TestSlots.Id}), {"Name": ~TestSlots.Name})

	async def test_contracts_and_delegation(self):
		original = lift(lambda value: value)
		stage(original, input=Tuple(Int), output=Int)
		self.assertIsNone(original.input)
		self.assertFalse(original.explicit)
		class Pipeline(stages):
			ContractIncrement = stage(lift(lambda value: value + 1), input=Tuple(Int), output=Int)
			def ContractEntry(env, value):
				return (yield from Pipeline.ContractIncrement(env, value))
		self.assertEqual(await run({}, Pipeline.ContractEntry, 1), 2)
		with self.assertRaises(Failure) as caught:
			await run({}, Pipeline.ContractEntry, "bad")
		self.assertIs(caught.exception.kind, Failure.Contract)
		self.assertEqual(caught.exception.trace[:2], ["ContractEntry", "ContractIncrement"])
		with self.assertRaises(Failure) as caught:
			await run({"policy": {"contracts": Checks.Off}}, Pipeline.ContractIncrement, "bad")
		self.assertIs(caught.exception.kind, Failure.Contract)
		with self.assertRaises(Failure) as caught:
			await run({}, stage(lift(lambda value: "wrong"), input=Tuple(Int), output=Int), 1)
		self.assertIs(caught.exception.kind, Failure.Defect)

	async def test_choose_admit_and_bound(self):
		route = choose(when(Int, lift(lambda value: value + 1)), otherwise(lift(lambda value: "fallback")))
		self.assertEqual(await run({}, route, 1), 2)
		self.assertEqual(await run({}, route, "text"), "fallback")
		with self.assertRaises(TypeError):
			when(TestSlots.Id, lift(lambda value: value))
		result = await run({}, collect(admit((Int, TestReason.Rejected))), [1, "bad"])
		self.assertEqual(result["value"]["Admitted"], [1])
		self.assertEqual(result["ledger"][0]["reason"], str(TestReason.Rejected))
		with self.assertRaises(Failure) as caught:
			await run({"policy": {"maxItems": 1}}, bound("maxItems", len), [1, 2])
		self.assertIs(caught.exception.kind, Failure.Budget)

	async def test_fold_and_tap(self):
		def Accumulate(env, accumulator, value):
			accumulator.append(value)
			return (yield accumulator)
		folded = fold(lambda env: [], Accumulate)
		self.assertEqual(await run({}, folded, [1, 2]), [1, 2])
		self.assertEqual(await run({}, folded, [3]), [3])
		value = {"items": [1]}
		def Publish(env, copy):
			copy["items"].append(2)
			return (yield copy)
		self.assertIs(await run({}, tap(Publish), value), value)
		self.assertEqual(value, {"items": [1]})

	async def test_always_preserves_primary_failure(self):
		calls = []
		def Cleanup(env, state):
			calls.append(state)
			raise Gap(TestReason.Cleanup)
			yield
		with self.assertRaises(Failure) as caught:
			await run({}, always(Missing, Cleanup), 1)
		self.assertIs(caught.exception.code, TestReason.Missing)
		self.assertIs(caught.exception.cleanup.code, TestReason.Cleanup)
		self.assertEqual(calls[0]["failure"]["kind"], "Gap")
		self.assertEqual(await run({}, always(lift(lambda value: value + 1), lift(lambda state: calls.append(state))), 1), 2)

	async def test_ledger_dedup_and_budget(self):
		class Pipeline(stages):
			def LedgerEntry(env):
				yield report(Ledger.Gap, target=Pipeline.LedgerEntry, reason=TestReason.Missing)
				yield report(Ledger.Gap, target=Pipeline.LedgerEntry, reason=TestReason.Missing, fatal=True)
				yield report(Ledger.Gap, target=Pipeline.LedgerEntry, reason=TestReason.Missing)
				return 1
			LedgerCollected = collect(LedgerEntry)
		result = await run({}, Pipeline.LedgerCollected)
		self.assertEqual(len(result["ledger"]), 1)
		self.assertTrue(result["ledger"][0]["fatal"])
		with self.assertRaises(Failure) as caught:
			await run({"policy": {"maxGaps": 0}}, Pipeline.LedgerCollected)
		self.assertIs(caught.exception.kind, Failure.Budget)

# EOF
