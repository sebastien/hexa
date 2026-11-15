"""Module: test_structure_parity — Shared API, identity and capture regression vectors."""

from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path

from hexa.combinators import backoff, lift, stages
from hexa.core import REGISTRY
from hexa.runtime import Runtime
from hexa.shapes import (
	Any, Failure, Int, Issue, Text, capture, codec, codes, decode, either, encode, errors, evidence, gather, lossy, pattern, request, slot, slots, transform, via, where,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "structure-parity.json").read_text())


class StructureParityTests(unittest.TestCase):
	"""Run identical binding-contract vectors without coupling native representations."""

	def test_public_exports(self):
		for name, expected in FIXTURE["exports"].items():
			module = importlib.import_module("hexa." + name)
			self.assertEqual(sorted(module.__all__), sorted(expected))

	def test_constraint_identity(self):
		def predicate(value):
			return value > 0
		constraint = where(predicate, "ParityPositive")
		self.assertIs(where(predicate, "ParityPositive"), constraint)
		with self.assertRaises(Failure) as caught:
			where(lambda value: value < 0, "ParityPositive")
		self.assertIs(caught.exception.code, Runtime.NameRedeclared)
		with self.assertRaises(TypeError):
			where(42, "ParityNonCallable")

	def test_library_namespaces(self):
		with self.assertRaises(Failure) as caught:
			codes("Issue", "ParityOther")
		self.assertIs(caught.exception.code, Runtime.NameRedeclared)
		self.assertIs(decode("$Issue"), Issue)

	def test_reserved_namespace_members(self):
		declarations = {
			"Codes": lambda name, member: codes(name, "Good", member),
			"Slots": lambda name, member: slots(name, **{"Good": Text, member: Text}),
			"Codec": lambda name, member: codec(name, source=Text, canonical=Text, map={"A": "a"}, gaps={"missing": member}),
			"Transform": lambda name, member: transform(name, lambda value: value, inputs=[Text], output=Text, gaps=["Good", member]),
			"Request": lambda name, member: request(name, versions=[1], options={}, validate=lambda options, value: value, compile=lambda: {}, gaps=["Good", member]),
		}
		for index, member in enumerate(FIXTURE["reservedNamespaceMembers"]):
			for kind, declare in declarations.items():
				with self.subTest(member=member, kind=kind):
					name = f"ParityReserved{kind}{index}"
					with self.assertRaises((TypeError, ValueError)):
						declare(name, member)
					self.assertFalse(any(key == name or key.startswith(name + ".") for key in REGISTRY))

	def test_reserved_stage_members(self):
		for index, member in enumerate(FIXTURE["reservedNamespaceMembers"]):
			with self.subTest(member=member):
				name = f"ParityReservedStage{index}"
				with self.assertRaises(ValueError):
					type("ParityStageNamespace", (stages,), {
						"__module__": __name__,
						name: lift(lambda value: value),
						member: lift(lambda value: value),
					})
				self.assertNotIn(name, REGISTRY)

	def test_valid_namespace_members(self):
		reasons = codes("ParityValidCodeMembers", *FIXTURE["validNamespaceMembers"])
		fields = slots("ParityValidSlotMembers", **dict.fromkeys(FIXTURE["validNamespaceMembers"], Text))
		for name in FIXTURE["validNamespaceMembers"]:
			self.assertEqual(encode(reasons)[name], f"$ParityValidCodeMembers.{name}")
			self.assertIs(decode(f"$ParityValidCodeMembers.{name}"), getattr(reasons, name))
			self.assertEqual(getattr(fields, name).name, f"ParityValidSlotMembers.{name}")

	def test_local_capture_names(self):
		for name in FIXTURE["captureNames"]:
			with self.subTest(name=name):
				captures = capture("ok", either(pattern("{" + name + "}"), Text))
				self.assertEqual(captures[name], "ok")
				self.assertEqual(evidence(captures, name), {"raw": "ok", "location": ""})

	def test_codec_instance_identity(self):
		family = codec("ParityFamily", params={"value": Any}, source=Text, canonical=Text,
			parse=lambda argument, value: value, format=lambda argument, value, original=None: value)
		for item in FIXTURE["codecArguments"]:
			instance = family(item["value"])
			self.assertEqual(instance.name, "ParityFamily" + item["suffix"])
			self.assertIs(decode({"$": "ParityFamily", "arguments": [item["value"]]}), instance)
		self.assertIs(family(1), family(1.0))

	def test_any_data_domain(self):
		for value in (float("nan"), float("inf"), lambda: None):
			self.assertTrue(errors(value, Any))

	def test_failed_union_isolation(self):
		items = slot("ParityUnionItems", [Text])
		captures = capture([{"value": "a", "ok": 1}, {"value": "b", "ok": "bad"}, {"value": "c", "ok": 1}],
			[either({"value": gather(items), "ok": Int}, Any)])
		self.assertEqual(captures[items], ["a", "c"])
		self.assertEqual(evidence(captures, items)["raw"], ["a", "c"])

	def test_nested_union_gather_state(self):
		items = slot("ParityLossItems", [Text])
		conversion = codec("ParityLossCodec", source=either(Text, Int), canonical=Text, losses=["Discarded"],
			parse=lambda value: lossy(str(value), conversion.Discarded), format=lambda value, original=None: value)
		captures = capture(["a", 1, "c"], [gather(via(items, conversion))])
		self.assertEqual(captures[items], ["a", "1", "c"])
		self.assertEqual(len(captures.losses), 3)
		self.assertEqual(evidence(captures, items)["codec"], [conversion.name] * 3)

	def test_pure_output_failure(self):
		value = 0
		for _ in range(102):
			value = [value]
		conversion = codec("ParityDeepOutput", source=Text, canonical=Any, parse=lambda _: value, format=lambda _, original=None: "")
		with self.assertRaises(Failure) as caught:
			capture("", via(slot("ParityOutput", Any), conversion))
		self.assertIs(caught.exception.kind, Failure.Defect)
		self.assertIs(caught.exception.code, Issue.Constraint)

	def test_backoff_validation(self):
		for value in (float("inf"), float("nan"), True):
			with self.assertRaises((TypeError, ValueError)):
				backoff(value)


# EOF
