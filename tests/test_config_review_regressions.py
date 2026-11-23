"""Module: test_config_review_regressions — Configuration boundary regressions."""

from __future__ import annotations

import subprocess
import sys
import unittest

from hexa.shapes import Failure, Int, Issue, Profile, SelectionRequest, Text, decode, encode, rest, transform


OPTIONS = {"versions": [1], "maxSelections": 2, "maxRelations": 1, "entitySets": {"People": []}, "relations": ["Membership"]}
CATALOG = {"entitySets": {"People": {"type": "Person", "attributes": {}}}, "relations": {"Membership": {"roles": {"member": "Person"}}}}


class ConfigReviewTests(unittest.TestCase):
	"""Check caller boundaries without changing execution behavior."""

	def test_transform_declaration(self):
		with self.assertRaises(TypeError):
			transform("PyConfigReviewNotCallable", 1, inputs=[], output=Text)
		for inputs in ([rest(Text), Int], [[Text, ...], Int]):
			with self.subTest(inputs=inputs), self.assertRaises(TypeError):
				transform("PyConfigReviewRest", lambda *values: "", inputs=inputs, output=Text)
		value = transform("PyConfigReviewValidRest", lambda *values: "".join(values), inputs=[rest(Text)], output=Text)
		self.assertEqual(value.inputs[0].kind, "rest")

	def test_reference_payload_requires_constructor(self):
		for option in ({"value": 1}, {"stage": "Text"}):
			with self.subTest(option=option), self.assertRaises(Failure) as caught:
				decode({"$": "Text", **option})
			self.assertEqual((caught.exception.kind, caught.exception.code), (Failure.Contract, Issue.Mismatch))

	def test_fresh_table_examples(self):
		encoded = {"$codec": {"name": "PyConfigReviewExamples", "source": "$Text", "canonical": "$Text",
			"map": {"A": "a"}, "gaps": {}, "examples": [{"input": "a", "output": "A"}]}}
		self.assertEqual(encode(decode(encoded)), encoded)

	def test_selection_contract_errors(self):
		for value in (None, [], 1):
			with self.subTest(value=value), self.assertRaises(Failure) as caught:
				SelectionRequest.validate(OPTIONS, value, CATALOG)
			self.assertEqual(caught.exception.issues, [{"code": Issue.Mismatch, "path": []}])
		with self.assertRaises(Failure) as caught:
			SelectionRequest.validate(OPTIONS, {}, CATALOG)
		self.assertEqual({tuple(item["path"]) for item in caught.exception.issues}, {("version",), ("selections",)})
		with self.assertRaises(Failure) as caught:
			SelectionRequest.validate(OPTIONS, {"version": True, "selections": {}, "relations": None}, CATALOG)
		self.assertEqual(caught.exception.issues, [
			{"code": Profile.Forbidden, "path": ["version"]},
			{"code": Issue.Constraint, "path": ["selections"]},
			{"code": Issue.Mismatch, "path": ["relations"]}])
		with self.assertRaises(Failure) as caught:
			SelectionRequest.validate(OPTIONS, {"version": 1,
				"selections": {"Person": {"from": [], "attributes": []}},
				"relations": [{"type": "Membership", "participants": {"member": "Person"}}]}, CATALOG)
		self.assertEqual(len(caught.exception.issues), 2)

	def test_selection_registration_import_order(self):
		result = subprocess.run([sys.executable, "-c", "import sys\nsys.path[:] = " + repr(sys.path) + "\n" + """
import importlib
from hexa.core import REGISTRY
importlib.import_module("hexa.profile")
assert "SelectionRequest" not in REGISTRY
selection = importlib.import_module("hexa.selection_request")
shapes = importlib.import_module("hexa.shapes")
assert shapes.SelectionRequest is selection.SelectionRequest
assert REGISTRY["SelectionRequest"] is selection.SelectionRequest
"""], capture_output=True, text=True)
		self.assertEqual(result.returncode, 0, result.stderr)

	def test_implicit_catalog_path_stops_at_first_missing_link(self):
		entry = lambda step: {"correspondences": [{"resolved": True, "supplier": step}]}
		catalog = {"steps": {"Root": {}, "Leaf": {}}, "relations": {"Group": entry("Root"), "Member": {}},
			"entitySets": {"People": {"type": "Person", "attributes": {"Name": {**entry("Leaf"), "via": ["Group", "Member"]}}}}}
		options = {**OPTIONS, "entitySets": {"People": ["Name"]}, "relations": []}
		request = SelectionRequest.validate(options, {"version": 1, "selections": {"P": {"from": "People", "attributes": ["Name"]}}}, catalog)
		compiled = SelectionRequest.compile(options, request, catalog)
		self.assertEqual(compiled["steps"], [{"step": "Root"}])
		self.assertEqual(compiled["gaps"], [{"code": SelectionRequest.Unmapped, "path": ["selections", "P", "attributes", 0, "via", 1]}])
		catalog["relations"]["Member"] = entry("Root")
		self.assertEqual(SelectionRequest.compile(options, request, catalog)["steps"], [{"step": "Root"}, {"step": "Leaf"}])


# EOF
