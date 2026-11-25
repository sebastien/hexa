/** Module: hexa.codec — Checked table and parameterized code codecs. */

import { Gap, Issue, NAME_RE, clone, freeze, jsonLiteral, namespace, register, validateData, withReasons } from "./core.js";
import { Codec, check, compileShape, errors } from "./shape.js";

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/** Declare a reversible table or pure-code conversion, optionally parameterized. */
export function codec(name, { source, canonical, parse, format, map, gaps = map ? {} : [], losses = [], params, examples = [] }) {
	if (!NAME_RE.test(name)) throw new TypeError("invalid codec name");
	source = compileShape(source);
	canonical = compileShape(canonical);
	const gapNames = [...new Set(["Unmapped", "Unformattable", ...(Array.isArray(gaps) ? gaps : Object.values(gaps))])];
	if ([...gapNames, ...losses].some(key => typeof key !== "string" || !NAME_RE.test(key) || key.includes("."))) throw new TypeError("invalid reason name");
	const reasons = namespace(name, [...gapNames, ...losses]);
	if (map !== undefined) {
		if (parse || format || params || losses.length || Array.isArray(gaps)) throw new TypeError("invalid table codec");
		gaps = clone(gaps);
		const reverse = new Map(), forward = new Map();
		for (const [key, sources] of Object.entries(map)) {
			if (errors(key, canonical).length) throw new TypeError("invalid canonical key");
			const items = Array.isArray(sources) ? sources : [sources];
			if (!items.length) throw new TypeError("empty alternatives");
			for (const item of items) {
				if (typeof item !== "string" || errors(item, source).length || reverse.has(item)) throw new TypeError("invalid table source");
				reverse.set(item, key);
			}
			forward.set(key, items[0]);
		}
		for (const key of Object.keys(gaps)) if (reverse.has(key)) throw new TypeError("duplicate gap source");
		parse = value => {
			if (Object.hasOwn(gaps, value)) throw new Gap(reasons[gaps[value]]);
			if (!reverse.has(value)) throw new Gap(reasons.Unmapped);
			return reverse.get(value);
		};
		format = (value, original) => {
			// Preserve the original spelling when it maps to the same canonical value.
			if (reverse.has(original) && reverse.get(original) === value) return original;
			if (!forward.has(value)) throw new Gap(reasons.Unformattable);
			return forward.get(value);
		};
	}
	if (typeof parse !== "function" || typeof format !== "function") throw new TypeError("codecs require parse and format");
	const definition = { name, source, canonical, parse, format, codecFamily: false, gaps: gapNames.map(key => reasons[key]), losses: losses.map(key => reasons[key]), examples: freeze(clone(examples)), arguments: [], family: null, table: map === undefined ? null : freeze(clone(map)), gapTable: map === undefined ? null : freeze(clone(gaps)) };
	if (params && Object.keys(params).length) {
		const parameters = Object.entries(params).map(([key, value]) => [key, compileShape(value)]);
		// Identical serialized arguments share a registered codec instance.
		const instances = new Map();
		const family = (...args) => {
			if (args.length !== parameters.length) throw new TypeError("wrong codec arity");
			args.forEach((value, index) => { validateData(value); check(value, parameters[index][1], Issue.Mismatch); });
			const key = jsonLiteral(args);
			if (!instances.has(key)) {
				const instanceName = `${name}(${key.slice(1, -1)})`;
				instances.set(key, register(instanceName, withReasons(new Codec({ ...definition, name: instanceName, arguments: freeze(clone(args)), family: result }), reasons)));
			}
			return instances.get(key);
		};
		Object.defineProperty(family, "name", { value: name, writable: true });
		Object.defineProperties(family, Object.fromEntries(Object.entries({ ...definition, params: parameters, codecFamily: true }).map(([key, value]) => [key, { value, enumerable: true, configurable: true }])));
		const result = withReasons(family, reasons);
		return register(name, result);
	}
	return register(name, withReasons(new Codec(definition), reasons));
}

// EOF
