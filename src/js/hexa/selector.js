/** Module: hexa.selector — Located selection with enclosing captures. */

import { Failure, Gap, Issue, pointer, unescapeSegment } from "./core.js";
import { SLOTS, Captures, capture, copyCaptures, loose, mergeCaptures, typeMatches } from "./shape.js";

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

function captureFields(segment) {
	return loose(Object.fromEntries(segment.slice(1, -1).split(",").map(item => {
		const parts = item.split(":$"), [key, reference] = parts;
		if (parts.length !== 2 || !SLOTS.has(reference)) throw new TypeError("invalid selector capture");
		return [key.trim(), SLOTS.get(reference)];
	})));
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/** Enumerate located occurrences with enclosing captures. */
export function matches(value, selector) {
	const segments = selector ? selector.split("/") : [], result = [];
	function visit(item, index, path, captures) {
		if (index === segments.length) {
			const entry = { value: item, location: pointer(path) };
			if (Object.keys(captures).length || Object.getOwnPropertySymbols(captures).length) entry.captures = captures;
			result.push(entry);
			return;
		}
		const segment = segments[index];
		if (segment.startsWith("{") && segment.endsWith("}")) {
			let inner;
			try { inner = mergeCaptures(capture(item, captureFields(segment)), captures); }
			catch (error) {
				if (error instanceof Failure && [Failure.Shape, Failure.Gap].includes(error.kind)) throw new Gap(error.code, { path: error.path, location: pointer(path) });
				throw error;
			}
			visit(item, index + 1, path, inner);
		} else if (segment === "*") {
			if (!typeMatches(item, "List")) throw new Gap(Issue.Mismatch, { path: [], location: pointer(path) });
			item.forEach((child, position) => visit(child, index + 1, [...path, position], copyCaptures(captures)));
		} else {
			const key = unescapeSegment(segment);
			if (!typeMatches(item, "Struct")) throw new Gap(Issue.Mismatch, { path: [], location: pointer(path) });
			if (!Object.hasOwn(item, key)) throw new Gap(Issue.Missing, { path: [key], location: pointer(path) });
			visit(item[key], index + 1, [...path, key], captures);
		}
	}
	visit(value, 0, [], new Captures());
	return result;
}

// EOF
