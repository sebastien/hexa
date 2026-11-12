"""Module: people_extract — Project mortgage clients using the Python binding.

PeopleExtract: MortgageRetrieve → MortgageValidate → MortgageClientsSelect → MortgageClientsProject
"""
from __future__ import annotations

from hexa.shapes import Any, Text, Int, DateTime, DateOnly, Positive, Null, loose, either, slots, codes, codec, Location
from hexa.combinators import stages, pipe, guard, select, each, project, collect, Policy
from hexa.runtime import join, p

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

# Host vocabulary, inlined for the example.
CountryCode = either("AU", "GB", "NZ")
CountryName = codec("CountryName", source=Text, canonical=CountryCode,
	map={"AU": "Australia", "GB": "United Kingdom", "NZ": ["New Zealand", "Aotearoa New Zealand"]})

Client = slots("Client",
	Id=Int & Positive, GivenNames=Text, FamilyName=Text,
	BirthDate=either(DateTime & DateOnly, Null), Country=CountryCode)
Mortgage = codes("Mortgage", "RootMismatch")

def RootBound(root):
	"""Bind the response's authority to `root`."""
	return loose({"application": loose({"id": root["mortgage"]}), "clients": [Any]})

ClientRow = loose({"id": Client.Id,
	"basicDetails": loose({"firstName": Client.GivenNames, "lastName": Client.FamilyName,
		"dateOfBirth?": Client.BirthDate, "countryOfBirth?": Client.Country @ CountryName})})
PersonRecord = {"GivenNames": Client.GivenNames, "FamilyName": Client.FamilyName,
	"BirthDate": ~Client.BirthDate, "Country": ~Client.Country, "Source": Location}

# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------

class People(stages):
	"""Extract person records, dropping malformed source rows."""

	def MortgageRetrieve(env):
		"""Root → {application, clients}, retrieved concurrently."""
		mortgage = env.root["mortgage"]
		responses = yield join({
			"Application": env.RequestSend({"method": "GET", "path": p(t"/mortgages/{mortgage}")}),
			"Clients": env.RequestSend({"method": "GET", "path": p(t"/mortgages/{mortgage}/clients")}),
		})
		return {"application": responses["Application"]["data"], "clients": responses["Clients"]["data"]}

	MortgageValidate = guard(lambda env: RootBound(env.root), Mortgage.RootMismatch)
	MortgageClientsSelect = select("clients/*")
	MortgageClientsProject = each(project(ClientRow, PersonRecord), Policy.Drop)
	PeopleExtract = collect(pipe(MortgageRetrieve, MortgageValidate,
		MortgageClientsSelect, MortgageClientsProject))

# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

def peopleExtractEnvironment(responses):
	"""Answer requests using a path-to-response mapping in `responses`."""
	return {"root": {"mortgage": 42}, "policy": {"maxGaps": 100, "maxConcurrency": 2},
		"RequestSend": lambda request, context: responses[request["path"]]}

# EOF
