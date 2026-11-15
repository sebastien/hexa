/** Module: run — Run the mortgage example against its shared JSON fixtures. */

import { readFile } from "node:fs/promises";

import { run } from "hexa/runtime.js";
import { People, peopleExtractEnvironment } from "./people_extract.js";

const responses = JSON.parse(await readFile(new URL("./fixtures/responses.json", import.meta.url), "utf8"));
console.log(JSON.stringify(await run(peopleExtractEnvironment(responses), People.PeopleExtract), null, 2));

// EOF
