"""PeopleExtract: MortgageRetrieve → MortgageValidate → MortgageClientsSelect → MortgageClientsProject

Targets the proposed binding (spec-003, Python 3.14+); not runnable until shapes.py,
combinators.py and runtime.py exist.
"""
from .shapes import Any, Text, Int, DateTime, DateOnly, Positive, Null, loose, either, slots, codes, codec, Location
from .combinators import stages, pipe, guard, select, each, project, collect, Policy
from .runtime import join, p

# Host vocabulary, inlined for the example.
CountryCode = either("AU", "GB", "NZ")
CountryName = codec("CountryName", source=Text, canonical=CountryCode,
    map={"AU": "Australia", "GB": "United Kingdom", "NZ": ["New Zealand", "Aotearoa New Zealand"]})

Client = slots("Client",
    Id=Int & Positive, GivenNames=Text, FamilyName=Text,
    BirthDate=either(DateTime & DateOnly, Null), Country=CountryCode)
Mortgage = codes("Mortgage", "RootMismatch")

def RootBound(Root):
    return loose({"application": loose({"id": Root["mortgage"]}), "clients": [Any]})

ClientRow = loose({"id": Client.Id,
    "basicDetails": loose({"firstName": Client.GivenNames, "lastName": Client.FamilyName,
        "dateOfBirth?": Client.BirthDate, "countryOfBirth?": Client.Country @ CountryName})})
PersonRecord = {"GivenNames": Client.GivenNames, "FamilyName": Client.FamilyName,
    "BirthDate": ~Client.BirthDate, "Country": ~Client.Country, "Source": Location}

class People(stages):
    """Each stage is named by its attribute name, through `__set_name__`."""

    def MortgageRetrieve(env):
        """Root → {application, clients}, retrieved concurrently."""
        mortgage = env.root["mortgage"]
        Responses = yield join({
            "Application": env.RequestSend({"method": "GET", "path": p(t"/mortgages/{mortgage}")}),
            "Clients": env.RequestSend({"method": "GET", "path": p(t"/mortgages/{mortgage}/clients")}),
        })
        return {"application": Responses["Application"]["data"], "clients": Responses["Clients"]["data"]}

    MortgageValidate = guard(lambda env: RootBound(env.root), Mortgage.RootMismatch)
    MortgageClientsSelect = select("clients/*")
    MortgageClientsProject = each(project(ClientRow, PersonRecord), Policy.Drop)
    PeopleExtract = collect(pipe(MortgageRetrieve, MortgageValidate,
        MortgageClientsSelect, MortgageClientsProject))

def people_extract_environment(responses):
    """Host side: answers each request from fixtures/responses.json."""
    return {"root": {"mortgage": 42}, "policy": {"maxGaps": 100, "maxConcurrency": 2},
            "RequestSend": lambda request, context: responses[request["path"]]}
# await run(people_extract_environment(responses), People.PeopleExtract)  ==  fixtures/expected.json
