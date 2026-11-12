"""Module: test_shapes — Check matching, captures, codecs and encodings."""

from __future__ import annotations

import json
import unittest

from hexa.shapes import (
	Any, Bool, DateOnly, DatePattern, DateTime, Decimal, DecimalText, Failure,
	Float, Gap, Instant, InstantOffset, Int, Issue, Nonempty, Null, Positive, Text, Tuple, __,
	capture, check, codec, codes, decode, derive, either, encode, errors, evidence,
	expand, gather, loose, matches, parsed, pattern, refine, rest, reshape,
	slot, slots, tolerant, transform, where,
)
from hexa.runtime import Runtime

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

TestSlots = slots("ShapeTestSlots", Id=Int, Name=Text, Country=Text, Items=[Text], Date=DateTime & DateOnly)
TestReason = codes("ShapeTest", "Invalid")

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------

class ShapeTests(unittest.TestCase):
	"""Check JSON semantics and value-free diagnostics."""

	def test_numbers(self):
		for value in (1, 1.0, -0.0, 2**53 - 1):
			self.assertEqual(errors(value, Int), [])
		for value in (True, "1", 1.5, 2**53, float("inf"), float("nan")):
			self.assertTrue(errors(value, Int))
		self.assertTrue(errors(1.0, Float))
		self.assertEqual(errors(1.5, Float), [])
		self.assertTrue(errors(1, Bool))
		self.assertTrue(errors(True, 1))
		self.assertIs(type(capture(-0.0, TestSlots.Id)[TestSlots.Id]), int)

	def test_structs(self):
		issues = errors({"b": None, "c": 1}, {"a": Text, "b?": Text})
		self.assertEqual([issue["code"] for issue in issues], [Issue.Missing, Issue.Mismatch, Issue.Unexpected])
		self.assertEqual(errors({"b": None}, {"b?": either(Text, Null)}), [])
		self.assertEqual(errors({"a": "ok", "extra": 1}, loose({"a": Text})), [])
		self.assertTrue(errors((1, 2), Tuple(Int, Int)))
		with self.assertRaises(TypeError):
			errors([], [])

	def test_slots_and_join(self):
		self.assertIs(slot("ShapeTestSlots.Id", Int), TestSlots.Id)
		with self.assertRaises(Failure) as caught:
			slots("ShapeTestSlots", Id=Text)
		self.assertIs(caught.exception.code, Runtime.SlotRedeclared)
		slot("ShapeTestLiteral", 1)
		self.assertIs(slot("ShapeTestLiteral", 1.0), slot("ShapeTestLiteral", 1))
		with self.assertRaises(Failure) as caught:
			slot("ShapeTestLiteral", True)
		self.assertIs(caught.exception.code, Runtime.SlotRedeclared)
		with self.assertRaises(AttributeError) as caught:
			TestSlots.Missing
		self.assertIs(caught.exception.code, Runtime.SlotUnknown)
		with self.assertRaises(AttributeError):
			TestSlots.Id = Text
		self.assertEqual(capture({"a": 1, "b": 1.0}, {"a": TestSlots.Id, "b": TestSlots.Id})[TestSlots.Id], 1)
		with self.assertRaises(Failure) as caught:
			capture({"a": 1, "b": 2}, {"a": TestSlots.Id, "b": TestSlots.Id})
		self.assertIs(caught.exception.code, Issue.CaptureConflict)
		self.assertEqual(caught.exception.path, ["b"])

	def test_tuple_rest(self):
		self.assertEqual(errors([1, 2, {}, "end"], Tuple(Int, Int, Any, Text)), [])
		for value in ([1, 2, "end"], [1, 2, None, True, "end"]):
			self.assertEqual(errors(value, Tuple(Int, Int, __, Text)), [])
		self.assertTrue(errors([1], Tuple(Int, __, Text)))
		self.assertTrue(errors([1, 2, "x"], Tuple(Int, rest(Text))))
		captures = capture([1, "a", "b", 2], Tuple(Int, rest(TestSlots.Items), Int))
		self.assertEqual(captures[TestSlots.Items], ["a", "b"])
		self.assertEqual(evidence(captures, TestSlots.Items)["location"], ["/1", "/2"])
		self.assertEqual(capture([1, 2], Tuple(Int, rest(TestSlots.Items), Int))[TestSlots.Items], [])
		with self.assertRaises(TypeError):
			Tuple(__, __)
		with self.assertRaises(TypeError):
			capture([], rest(Any))

	def test_collections(self):
		self.assertEqual(capture(["a", "b"], [TestSlots.Items])[TestSlots.Items], ["a", "b"])
		shape = {"rows?": [loose({"name?": gather(TestSlots.Items)})]}
		self.assertNotIn(TestSlots.Items, capture({}, shape))
		self.assertEqual(capture({"rows": []}, shape)[TestSlots.Items], [])
		self.assertEqual(capture({"rows": [{}, {"name": "a"}]}, shape)[TestSlots.Items], ["a"])
		captures = capture(["a", "b"], [TestSlots.Items])
		self.assertEqual(expand([TestSlots.Items], captures), (["a", "b"], []))
		self.assertEqual(expand(decode(encode([TestSlots.Items])), captures), (["a", "b"], []))
		with self.assertRaises(TypeError):
			capture([{"a": "a", "b": "b"}], [{"a": gather(TestSlots.Items), "b": gather(TestSlots.Items)}])

	def test_codecs_and_evidence(self):
		country = codec("ShapeCountry", source=Text, canonical=Text, map={"NZ": ["New Zealand", "Aotearoa"]}, gaps={"Unknown": "NotStated"})
		captures = capture("Aotearoa", TestSlots.Country @ country)
		self.assertEqual(captures[TestSlots.Country], "NZ")
		self.assertEqual(expand(TestSlots.Country @ country, captures)[0], "Aotearoa")
		self.assertEqual(expand(parsed(TestSlots.Country), captures)[0], "ShapeCountry")
		self.assertEqual(evidence(captures, TestSlots.Country), {"raw": "Aotearoa", "location": "", "codec": "ShapeCountry"})
		for value, reason in (("Narnia", country.Unmapped), ("Unknown", country.NotStated)):
			with self.assertRaises(Gap) as caught:
				capture(value, TestSlots.Country @ country)
			self.assertIs(caught.exception.code, reason)
		with self.assertRaises(ValueError):
			codec("ShapeDuplicate", source=Text, canonical=Text, map={"a": "same", "b": "same"})

	def test_reshape(self):
		self.assertEqual(reshape({"name": TestSlots.Name}, {"Name": TestSlots.Name, "Id": TestSlots.Id}, {"name": "Ada"}, {TestSlots.Id: 7}), {"Name": "Ada", "Id": 7})
		with self.assertRaises(Failure) as caught:
			reshape({"name": TestSlots.Name}, {"Name": TestSlots.Name}, {"name": "Ada"}, {TestSlots.Name: "Bea"})
		self.assertIs(caught.exception.code, Issue.CaptureConflict)

	def test_tolerant_holes(self):
		captures = capture({"name": 3}, {"name": tolerant(TestSlots.Name), "country?": TestSlots.Country})
		value, holes = expand({"Name": ~TestSlots.Name, "Country": ~TestSlots.Country, "Fixed": 1}, captures)
		self.assertEqual(value, {"Fixed": 1})
		self.assertEqual(holes, [{"path": ["Name"], "reason": Issue.Mismatch}])
		value, holes = expand([10, TestSlots.Name, 30], capture({}, {}))
		self.assertEqual(value, {0: 10, 2: 30})
		self.assertEqual(holes[0]["path"], [1])
		self.assertEqual(json.loads(json.dumps(value)), {"0": 10, "2": 30})
		with self.assertRaises(Gap):
			expand(TestSlots.Name, {})
		captures = capture([{"name": "a"}, {"name": 3}, {"name": "b"}], [{"name": tolerant(gather(TestSlots.Items))}])
		self.assertNotIn(TestSlots.Items, captures)
		self.assertEqual(expand({"Names": ~TestSlots.Items}, captures)[0], {})

	def test_copying_and_contracts(self):
		local = slots("ShapeMutable", Data=Any)
		original = {"a": [1]}
		captures = capture(original, local.Data)
		first = expand({"one": local.Data, "two": local.Data}, captures)[0]
		first["one"]["a"].append(2)
		self.assertEqual(original, {"a": [1]})
		self.assertEqual(first["two"], original)
		self.assertIs(check(original, Any, TestReason.Invalid), original)
		with self.assertRaises(Failure) as caught:
			check("wrong", Int, TestReason.Invalid)
		self.assertIs(caught.exception.kind, Failure.Contract)
		self.assertIs(caught.exception.code, TestReason.Invalid)

	def test_derivations(self):
		compose = transform("ShapeCompose", lambda name, country: name + "/" + country, inputs=[Text, Text], output=Text)
		template = {"Display": derive(compose, TestSlots.Name, TestSlots.Country)}
		captures = capture({"name": "A", "country": "NZ"}, {"name": TestSlots.Name, "country": TestSlots.Country})
		self.assertEqual(expand(template, captures), ({"Display": "A/NZ"}, []))
		self.assertEqual(len(expand(template, {})[1]), 1)
		self.assertEqual(encode(decode(encode(derive(compose, TestSlots.Name, TestSlots.Country))))["$derive"], "$ShapeCompose")
		with self.assertRaises(TypeError):
			derive(compose, TestSlots.Id, TestSlots.Country)
		checked = transform("ShapeChecked", lambda name: name, inputs=[Text], output=Text, gaps=["Blank"])
		self.assertEqual(str(checked.Blank), "ShapeChecked.Blank")
		with self.assertRaises(AttributeError) as caught:
			checked.Other
		self.assertIs(caught.exception.code, Runtime.MemberUnknown)
		joined = transform("ShapeJoin", lambda *values: "/".join(values), inputs=[[Text, ...]], output=Text)
		self.assertEqual(expand(derive(joined, "a", "b", "c"), {})[0], "a/b/c")
		self.assertEqual(expand(derive(joined), {})[0], "")

	def test_union_transaction(self):
		bad = slots("ShapeUnion", First=Text, Second=Text)
		captures = capture({"a": "ok", "b": 1}, either({"a": bad.First, "b": Text}, {"a": bad.Second, "b": Int}))
		self.assertNotIn(bad.First, captures)
		self.assertEqual(captures[bad.Second], "ok")

	def test_dates_and_exact_codecs(self):
		for value in ("2026-11-10", "2024-02-29", "2026-11-10T14:30:05.123+13:00"):
			self.assertEqual(errors(value, DateTime), [])
		for value in ("2026-02-29", "2026-11-10Z", "2026-11-10T24:00", "2026-W01-1", "2026-11-10T23:59:60Z"):
			self.assertTrue(errors(value, DateTime))
		self.assertEqual(capture("20260915", TestSlots.Date @ DatePattern("YYYYMMDD"))[TestSlots.Date], "2026-09-15")
		self.assertIs(DatePattern("YYYYMMDD"), DatePattern("YYYYMMDD"))
		self.assertIs(DatePattern("YYYYMMDD").family, DatePattern)
		with self.assertRaises(TypeError):
			TestSlots.Date @ DatePattern
		with self.assertRaises(TypeError):
			DecimalText("unexpected")
		amount = slots("ShapeAmount", Value=Decimal)
		for raw, expected in (("1.2500e3", "1250"), ("-0.000", "0"), ("0.00100", "0.001"), (12.5, "12.5")):
			self.assertEqual(capture(raw, amount.Value @ DecimalText)[amount.Value], expected)
		instant = slots("ShapeInstant", Value=Instant)
		captures = capture("2026-01-01T00:30:00.123456789+01:00", instant.Value @ InstantOffset)
		self.assertEqual(captures[instant.Value], "2025-12-31T23:30:00.123456789Z")
		self.assertEqual(captures.losses[0]["reason"], InstantOffset.Discarded)

	def test_patterns(self):
		integerText = codec("ShapeIntegerText", source=Text, canonical=Int, parse=lambda value: int(value), format=lambda value, original=None: str(value))
		captures = capture("Ada-42", pattern("{Name}-{ShapeTestSlots.Id:ShapeIntegerText}"))
		self.assertEqual(captures["Name"], "Ada")
		self.assertEqual(captures[TestSlots.Id], 42)
		self.assertEqual(evidence(captures, TestSlots.Id)["codec"], integerText.name)
		self.assertTrue(errors("extra/Ada-42", pattern("Ada-{Id}")))

	def test_selectors(self):
		values = {"parents": [{"id": 3, "children": ["a", "b"]}]}
		selected = matches(values, "parents/*/{id:$ShapeTestSlots.Id}/children/*")
		self.assertEqual([item["location"] for item in selected], ["/parents/0/children/0", "/parents/0/children/1"])
		self.assertEqual(selected[0]["captures"][TestSlots.Id], 3)
		self.assertEqual(matches({"items": []}, "items/*"), [])
		with self.assertRaises(Gap):
			matches({}, "items/*")
		self.assertEqual(matches({"a/b": {"~": [1]}}, "a~1b/~0/*")[0]["location"], "/a~1b/~0/0")

	def test_encoding(self):
		values = [Tuple(Int, __, Text), refine(Int, Positive), loose({"id": TestSlots.Id}), tolerant(TestSlots.Name), parsed(TestSlots.Name), pattern("{Name}"), either(Text, Int)]
		for value in values:
			with self.subTest(value=encode(value)):
				self.assertEqual(encode(decode(encode(value))), encode(value))
		self.assertEqual(encode(__), {"$rest": "$Any"})
		self.assertEqual(expand(decode({"$$literal?": "$$Text"}), {})[0], {"$literal?": "$Text"})
		with self.assertRaises(Failure):
			decode("$Unknown")
		with self.assertRaises(Failure):
			decode("$Text", registry={})
		with self.assertRaises(Failure) as caught:
			decode({"$rest": "$Any", "unexpected": 1})
		self.assertIs(caught.exception.code, Issue.Unexpected)
		for malformed in ({"$loose": "$Text"}, {"$refine": "$Int", "constraints": ["$Text"]}, {"$unknown": 1}, {"$": "Text", "arguments": [1]}):
			with self.subTest(malformed=malformed), self.assertRaises(Failure) as caught:
				decode(malformed)
			self.assertIs(caught.exception.kind, Failure.Contract)
		with self.assertRaises(Failure):
			decode([[[[1]]]], maxDepth=2)
		cyclic = []
		cyclic.append(cyclic)
		with self.assertRaises(Failure):
			decode(cyclic)
		with self.assertRaises(Failure):
			decode({"$pattern": "{ShapeTestSlots.Id}"}, registry={})
		value = {"items": [10, TestSlots.Name, 30]}
		self.assertEqual(expand(decode(encode(value)), {})[0], {"items": {0: 10, 2: 30}})

	def test_constraint_operators(self):
		even = where(lambda value: value % 2 == 0, "ShapeEven")
		self.assertEqual(errors(3, Int & even)[0]["constraint"], "ShapeEven")
		with self.assertRaises(TypeError):
			where(lambda value: True, TestReason.Invalid)
		self.assertEqual(errors(5, Int & Positive), [])
		self.assertEqual(errors(-1, Int & Positive)[0]["constraint"], "Positive")
		self.assertEqual(errors([1], [Int] & Nonempty), [])
		self.assertEqual(errors([], [Int] & Nonempty)[0]["code"], Issue.Constraint)
		self.assertEqual(capture(3, TestSlots.Id & Positive)[TestSlots.Id], 3)
		with self.assertRaises(Failure):
			capture(-3, TestSlots.Id & Positive)

	def test_codec_encoding(self):
		country = codec("ShapeEncodedCountry", source=Text, canonical=Text, map={"NZ": ["New Zealand", "Aotearoa"]}, gaps={"Unknown": "NotStated"})
		encoded = encode(country)
		self.assertEqual(encoded["$codec"]["map"], {"NZ": ["New Zealand", "Aotearoa"]})
		self.assertIs(decode(encoded), country)
		altered = json.loads(json.dumps(encoded))
		altered["$codec"]["map"]["AU"] = "Australia"
		with self.assertRaises(Failure) as caught:
			decode(altered)
		self.assertIs(caught.exception.code, Issue.Mismatch)
		altered["$codec"]["name"] = "ShapeDecodedCountry"
		declared = decode(altered)
		self.assertEqual(capture("Australia", TestSlots.Country @ declared)[TestSlots.Country], "AU")
		self.assertIs(decode("$ShapeDecodedCountry"), declared)
		with self.assertRaises(Failure) as caught:
			decode({"$codec": {"name": "ShapeUndeclared", "source": "$Text", "canonical": "$Text", "gaps": [], "losses": []}})
		self.assertIs(caught.exception.code, Runtime.MemberUnknown)
		self.assertEqual(encode(TestSlots.Date @ DatePattern("YYYYMMDD")), {"$": "ShapeTestSlots.Date", "via": {"$": "DatePattern", "arguments": ["YYYYMMDD"]}})
		self.assertIs(decode(encode(TestSlots.Date @ DatePattern("YYYYMMDD"))).args[1], DatePattern("YYYYMMDD"))

	def test_codec_defects_and_declarations(self):
		bad = codec("ShapeBadCodec", source=Text, canonical=Text, parse=lambda value: 3, format=lambda value, original=None: value)
		with self.assertRaises(Failure) as caught:
			capture("source", TestSlots.Name @ bad)
		self.assertIs(caught.exception.kind, Failure.Defect)
		def undeclared(value):
			raise Gap(TestReason.Invalid)
		bad = codec("ShapeUndeclaredCodec", source=Text, canonical=Text, parse=undeclared, format=lambda value, original=None: value)
		with self.assertRaises(Failure) as caught:
			capture("source", TestSlots.Name @ bad)
		self.assertIs(caught.exception.kind, Failure.Defect)
		with self.assertRaises(TypeError):
			capture("1", TestSlots.Id @ DecimalText)

	def test_standard_examples(self):
		for item in (DecimalText, InstantOffset, DatePattern):
			for example in item.examples:
				instance = item(*example["arguments"]) if "arguments" in example else item
				result = instance.convert(example["input"])
				self.assertEqual(getattr(result, "value", result), example["output"])
				if example.get("direction", "both") == "both":
					self.assertEqual(instance.convert(example["output"], formatting=True), example["input"])

# EOF
