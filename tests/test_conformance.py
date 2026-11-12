"""Module: test_conformance — Execute the shared mortgage fixtures unchanged."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from hexa.combinators import pipe, select, stages
from hexa.runtime import inert, join, run
from hexa.shapes import decode, encode

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "mortgage-people"
moduleSpec = importlib.util.spec_from_file_location("mortgage_example", EXAMPLE_PATH / "people_extract.py")
Example = importlib.util.module_from_spec(moduleSpec)
moduleSpec.loader.exec_module(Example)


def fixture(name):
	return json.loads((EXAMPLE_PATH / "fixtures" / f"{name}.json").read_text())

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------

class ConformanceTests(unittest.IsolatedAsyncioTestCase):
	"""Use one fixture set for live operations, replay and encodings."""

	async def test_live(self):
		env = Example.peopleExtractEnvironment(fixture("responses"))
		self.assertEqual(await run(env, Example.People.PeopleExtract), fixture("expected"))

	async def test_replay(self):
		def forbidden(*args):
			self.fail("replay called a host operation")
		env = {"root": {"mortgage": 42}, "replay": fixture("journal"), "RequestSend": forbidden}
		self.assertEqual(await run(env, Example.People.PeopleExtract), fixture("expected"))

	async def test_origins(self):
		class Provenance(stages):
			ProvenanceRowsSelect = select("clients/*")
			def ProvenanceRetrieve(env):
				return (yield from Example.People.MortgageRetrieve(env))
			ProvenanceEntry = pipe(ProvenanceRetrieve, ProvenanceRowsSelect)
		env = {"root": {"mortgage": 42}, "replay": fixture("journal")}
		occurrences = await run(env, Provenance.ProvenanceEntry)
		self.assertEqual(occurrences[0]["origin"], {"strand": "/Clients", "seq": 0, "pointer": "/data/0"})

	def test_inert(self):
		env = inert({"root": {"mortgage": 42}})
		iterator = Example.People.MortgageRetrieve(env)
		self.assertEqual(next(iterator), join({
			"Application": env.RequestSend({"method": "GET", "path": "/mortgages/42"}),
			"Clients": env.RequestSend({"method": "GET", "path": "/mortgages/42/clients"}),
		}))
		iterator.close()
		effect = env.RequestSend({"method": "GET", "path": "/mortgages/42"})
		self.assertEqual(encode(effect), {"operation": "RequestSend", "arguments": [{"method": "GET", "path": "/mortgages/42"}]})

	def test_encodings(self):
		for name, expected in fixture("shapes").items():
			self.assertEqual(encode(getattr(Example, name)), expected)
			self.assertEqual(encode(decode(expected)), expected)

# EOF
