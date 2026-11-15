/** Module: people_extract — Project mortgage clients into person records. */

import { Any, Text, Int, DateTime, DateOnly, Positive, Null, loose, either, refine, slots, codes, optional, via, codec, Location } from "hexa/shapes.js";
import { stages, pipe, guard, select, each, project, collect, Policy } from "hexa/combinators.js";
import { join, p } from "hexa/runtime.js";

// Host vocabulary, inlined for the example.
const CountryCode = either("AU", "GB", "NZ");
const CountryName = codec("CountryName", { source: Text, canonical: CountryCode,
	map: { AU: "Australia", GB: "United Kingdom", NZ: ["New Zealand", "Aotearoa New Zealand"] } });

const Client = slots("Client", {
	Id: refine(Int, Positive), GivenNames: Text, FamilyName: Text,
	BirthDate: either(refine(DateTime, DateOnly), Null), Country: CountryCode,
});
const Mortgage = codes("Mortgage", "RootMismatch");

const RootBound = Root => loose({ application: loose({ id: Root.mortgage }), clients: [Any] });
const ClientRow = loose({ id: Client.Id,
	basicDetails: loose({ firstName: Client.GivenNames, lastName: Client.FamilyName,
		"dateOfBirth?": Client.BirthDate, "countryOfBirth?": via(Client.Country, CountryName) }) });
const PersonRecord = { GivenNames: Client.GivenNames, FamilyName: Client.FamilyName,
	BirthDate: optional(Client.BirthDate), Country: optional(Client.Country), Source: Location };

// Each stage is named by its key; S references sibling stages.
export const People = stages(S => ({
	/** Root → {application, clients}, retrieved concurrently. */
	*MortgageRetrieve(env) {
		const { mortgage } = env.root;
		const { Application, Clients } = yield join({
			Application: env.RequestSend({ method: "GET", path: p`/mortgages/${mortgage}` }),
			Clients: env.RequestSend({ method: "GET", path: p`/mortgages/${mortgage}/clients` }),
		});
		return { application: Application.data, clients: Clients.data };
	},
	MortgageValidate: guard(env => RootBound(env.root), Mortgage.RootMismatch),
	MortgageClientsSelect: select("clients/*"),
	MortgageClientsProject: each(project(ClientRow, PersonRecord), Policy.Drop),
	PeopleExtract: collect(pipe(S.MortgageRetrieve, S.MortgageValidate,
		S.MortgageClientsSelect, S.MortgageClientsProject)),
}));

/** Host side: answers each request from fixtures/responses.json. */
export const peopleExtractEnvironment = responses => ({
	root: { mortgage: 42 }, policy: { maxGaps: 100, maxConcurrency: 2 },
	RequestSend: request => responses[request.path],
});
// await run(peopleExtractEnvironment(responses), People.PeopleExtract)  deep-equals  fixtures/expected.json

// EOF
