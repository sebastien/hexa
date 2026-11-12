"""Module: test_profiles — Check profiles, decoding under them, request formats and plans."""

from __future__ import annotations

import unittest

from hexa.combinators import pipe, plan, validate
from hexa.runtime import run
from hexa.shapes import (
	DateOnly, DatePattern, DateTime, Failure, Int, Issue, Positive, Profile, SelectionRequest, Text,
	capture, decode, describe, either, encode, loose, profile, refine, slots, transform,
)

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

Member = slots("ProfileTestMember", Id=Int & Positive, Name=Text, Born=DateTime & DateOnly)
Hidden = slots("ProfileTestHidden", Secret=Text)
compose = transform("ProfileTestCompose", lambda left, right: left + right, inputs=[Text, Text], output=Text)

SELECTION_OPTIONS = {"versions": [1], "maxSelections": 2, "maxRelations": 1,
	"entitySets": {"Clients": ["GivenNames", "Household", "Income"], "Households": []},
	"relations": ["HouseholdHasMemberPerson"]}

Caller = profile("ProfileTestCaller",
	constructors=[loose, either, refine],
	types=[Text, Int],
	shapes={"ProfileTestRow": loose({"id": Member.Id})},
	slots=[Member],
	constraints=[Positive],
	codecs=[DatePattern],
	requests={SelectionRequest: SELECTION_OPTIONS},
	budgets={"maxDepth": 8, "maxNodes": 200})

CATALOG = {
	"steps": {
		"MortgageRetrieve": {"capability": "mortgage_read"},
		"ClientsRetrieve": {"capability": "client_read", "requires": ["MortgageRetrieve"]},
		"HouseholdsRetrieve": {"capability": "household_read", "requires": ["MortgageRetrieve"]},
		"IncomeRetrieve": {"capability": "income_read"},
	},
	"entitySets": {
		"Clients": {"type": "Person", "requires": ["MortgageRetrieve"], "attributes": {
			"GivenNames": {"correspondences": [{"supplier": "ClientsRetrieve", "resolved": True}]},
			"Household": {"correspondences": [{"supplier": "HouseholdsRetrieve", "resolved": True}], "reference": "Household"},
			"Income": {"correspondences": [{"supplier": "IncomeRetrieve", "resolved": False}]},
			"Secret": {"correspondences": [{"supplier": "ClientsRetrieve", "resolved": True}]},
		}},
		"Households": {"type": "Household", "attributes": {}},
		"Advisers": {"type": "Person", "attributes": {}},
	},
	"relations": {
		"HouseholdHasMemberPerson": {"roles": {"source": "Household", "target": "Person"}, "correspondences": []},
	},
}


def environment(**policy):
	return {"policy": {"profile": Caller, **policy}, "vocabulary": {"SelectionRequest": CATALOG}}


def refused(test, value, definition=Caller):
	with test.assertRaises(Failure) as caught:
		decode(value, definition)
	return caught.exception

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------

class ProfileTests(unittest.TestCase):
	"""Check that profiles admit only what the host permits."""

	def test_permitted(self):
		shape = decode({"$loose": {"id": "$ProfileTestMember.Id", "born": {"$": "ProfileTestMember.Born", "via": {"$": "DatePattern", "arguments": ["YYYYMMDD"]}},
			"kind": {"$either": ["$Text", "$Int"]}}}, Caller)
		self.assertEqual(capture({"id": 1, "born": "20260915", "kind": 2}, shape)[Member.Born], "2026-09-15")
		self.assertIs(decode("$ProfileTestRow", Caller), decode("$ProfileTestRow"))
		self.assertEqual(encode(decode({"$refine": "$Int", "constraints": ["$Positive"]}, Caller)), {"$refine": "$Int", "constraints": ["$Positive"]})

	def test_forbidden(self):
		failure = refused(self, {"a": "$ProfileTestHidden.Secret", "b": "$Bool", "c": "$Unknown", "d": {"$tuple": ["$Text"]}})
		self.assertIs(failure.kind, Failure.Contract)
		self.assertIs(failure.code, Profile.Forbidden)
		self.assertEqual([issue["path"] for issue in failure.issues], [["a"], ["b"], ["c"], ["d", "$tuple"]])
		self.assertTrue(all(issue["code"] is Profile.Forbidden for issue in failure.issues))
		for value in ({"$": "Policy.Substitute", "value": 1}, {"operation": "Read", "arguments": []},
				{"$codec": {"name": "X", "source": "$Text", "canonical": "$Text", "map": {}}}, encode(Caller),
				{"$derive": "$ProfileTestCompose", "arguments": ["$ProfileTestMember.Name", "x"]},
				{"$pattern": "{ProfileTestHidden.Secret}"}, "$Policy.Drop"):
			with self.subTest(value=value):
				self.assertIs(refused(self, value).code, Profile.Forbidden)

	def test_unknown_and_forbidden_alike(self):
		unknown, hidden = refused(self, "$ProfileTestNothing"), refused(self, "$ProfileTestHidden.Secret")
		self.assertEqual((unknown.kind, unknown.code, unknown.issues), (hidden.kind, hidden.code, [{"code": Profile.Forbidden, "path": []}]))

	def test_budgets(self):
		failure = refused(self, [[[[[[[[[["$Text"]]]]]]]]]])
		self.assertEqual((failure.kind, failure.code), (Failure.Budget, Profile.Exceeded))
		self.assertIs(refused(self, {str(index): "$Text" for index in range(150)}).code, Profile.Exceeded)
		with self.assertRaises(ValueError):
			profile("ProfileTestLarge", budgets={"maxDepth": 10**6})

	def test_construction_after_permission(self):
		failure = refused(self, {"$loose": "$Text"})
		self.assertEqual((failure.kind, failure.code), (Failure.Contract, Issue.Mismatch))

	def test_declaration(self):
		self.assertIs(profile("ProfileTestCaller", constructors=[loose, either, refine], types=[Text, Int],
			shapes={"ProfileTestRow": loose({"id": Member.Id})}, slots=[Member], constraints=[Positive], codecs=[DatePattern],
			requests={SelectionRequest: SELECTION_OPTIONS}, budgets={"maxDepth": 8, "maxNodes": 200}), Caller)
		with self.assertRaises(Failure) as caught:
			profile("ProfileTestCaller", types=[Text])
		self.assertIs(caught.exception.kind, Failure.Defect)
		with self.assertRaises(TypeError):
			profile("ProfileTestBad", constructors=[capture])
		with self.assertRaises(ValueError):
			profile("ProfileTestBad", requests={SelectionRequest: {**SELECTION_OPTIONS, "versions": [2]}})
		with self.assertRaises(Failure):
			profile("ProfileTestBad", requests={SelectionRequest: {**SELECTION_OPTIONS, "entitySets": {"Clients": [1]}}})

	def test_encoding(self):
		encoded = encode(Caller)
		self.assertEqual(encoded["$profile"]["slots"], ["$ProfileTestMember"])
		self.assertEqual(encoded["$profile"]["requests"], {"SelectionRequest": SELECTION_OPTIONS})
		self.assertIs(decode(encoded), Caller)
		altered = {"$profile": {**encoded["$profile"], "types": ["$Text"]}}
		with self.assertRaises(Failure) as caught:
			decode(altered)
		self.assertIs(caught.exception.code, Issue.Mismatch)
		fresh = {"$profile": {**encoded["$profile"], "name": "ProfileTestFresh", "budgets": {"maxDepth": 4}}}
		self.assertEqual(decode(fresh).limits["maxDepth"], 4)

	def test_describe(self):
		description = describe(Caller)
		self.assertEqual(set(description["slots"]), {"ProfileTestMember.Id", "ProfileTestMember.Name", "ProfileTestMember.Born"})
		self.assertEqual(description["types"], ["$Text", "$Int"])
		self.assertEqual(list(description["codecs"]), ["DatePattern"])
		self.assertNotIn("ProfileTestHidden.Secret", str(description))
		self.assertEqual(description["requests"]["SelectionRequest"]["gaps"], ["Unmapped", "Unresolved", "NotSupplied", "ReferenceUnselected"])


class RequestTests(unittest.IsolatedAsyncioTestCase):
	"""Check request validation and plan compilation for selection requests."""

	async def prepare(self, request, **policy):
		return await run(environment(**policy), pipe(validate(SelectionRequest), plan(SelectionRequest)), request)

	async def refusal(self, request, **policy):
		with self.assertRaises(Failure) as caught:
			await self.prepare(request, **policy)
		return caught.exception

	async def test_plan(self):
		request = {"version": 1,
			"selections": {"People": {"from": "Clients", "attributes": ["GivenNames", "Household", "Income"]}},
			"relations": [{"type": "HouseholdHasMemberPerson", "participants": {"source": [], "target": "People"}}]}
		failure = await self.refusal(request)
		self.assertEqual(failure.issues, [{"code": Issue.Mismatch, "path": ["relations", 0, "participants", "source"]}])
		request["selections"]["Home"] = {"from": "Households", "attributes": []}
		request["relations"][0]["participants"]["source"] = "Home"
		result = await self.prepare(request)
		self.assertEqual(result["steps"], [{"step": "MortgageRetrieve"}, {"step": "ClientsRetrieve"}, {"step": "HouseholdsRetrieve"}])
		self.assertEqual(result["allow"], ["mortgage_read", "client_read", "household_read"])
		self.assertEqual(result["gaps"], [
			{"code": "SelectionRequest.Unresolved", "path": ["selections", "People", "attributes", 2]},
			{"code": "SelectionRequest.Unmapped", "path": ["relations", 0]}])
		self.assertEqual(result["request"]["relations"][0]["participants"], {"source": ["Home"], "target": ["People"]})
		self.assertEqual(result["budgets"]["maxGaps"], 1000)
		self.assertEqual(await self.prepare(request), result)

	async def test_reference_unselected(self):
		result = await self.prepare({"version": 1, "selections": {"People": {"from": "Clients", "attributes": ["Household"]}}})
		self.assertEqual(result["gaps"], [{"code": "SelectionRequest.ReferenceUnselected", "path": ["selections", "People", "attributes", 0]}])

	async def test_refusals(self):
		cases = [
			({"selections": {}}, Issue.Missing, ["version"]),
			({"version": 2, "selections": {}}, Profile.Forbidden, ["version"]),
			({"version": 1, "selections": {"People": {"from": "Advisers", "attributes": []}}}, Profile.Forbidden, ["selections", "People", "from"]),
			({"version": 1, "selections": {"People": {"from": "Clients", "attributes": ["Secret"]}}}, Profile.Forbidden, ["selections", "People", "attributes", 0]),
			({"version": 1, "selections": {"people": {"from": "Clients", "attributes": []}}}, Issue.Mismatch, ["selections", "people"]),
			({"version": 1, "selections": {}}, Issue.Constraint, ["selections"]),
			({"version": 1, "selections": {"People": {"from": "Clients", "attributes": []}}, "extra": 1}, Issue.Unexpected, ["extra"]),
			({"version": 1, "selections": {"People": {"from": "Clients", "attributes": ["Income", "Income"]}}}, Issue.Constraint, ["selections", "People", "attributes"]),
		]
		for request, code, path in cases:
			with self.subTest(request=request):
				failure = await self.refusal(request)
				self.assertEqual((failure.kind, failure.code, failure.path), (Failure.Contract, code, path))

	async def test_policy(self):
		request = {"version": 1, "selections": {"People": {"from": "Clients", "attributes": ["GivenNames"]}}}
		failure = await self.refusal(request, allow=["mortgage_read"])
		self.assertEqual((failure.kind, failure.code), (Failure.Contract, Profile.Forbidden))
		self.assertEqual(len((await self.prepare(request, allow=["mortgage_read", "client_read"]))["steps"]), 2)
		with self.assertRaises(Failure) as caught:
			await run({"policy": {}}, validate(SelectionRequest), request)
		self.assertIs(caught.exception.code, Profile.Forbidden)
		with self.assertRaises(Failure) as caught:
			await run({"policy": {"profile": "ProfileTestCaller"}}, validate(SelectionRequest), request)
		self.assertIs(caught.exception.code, Issue.Mismatch)

	async def test_budget(self):
		request = {"version": 1, "selections": {"People": {"from": "Clients", "attributes": ["GivenNames"] * 300}}}
		failure = await self.refusal(request)
		self.assertEqual((failure.kind, failure.code), (Failure.Budget, Profile.Exceeded))

# EOF
