/** Module: hexa.vocabulary — Standard constraints, codecs and provenance slots. */

import { Gap } from "./core.js";
import { codec } from "./codec.js";
import { DateTime, Decimal, Instant, Int, Num, Text, dateParts, daysInMonth, either, lossy, nullable, refine, slot, where } from "./shape.js";
import { transform } from "./template.js";

// -----------------------------------------------------------------------------
//
// UTILITIES
//
// -----------------------------------------------------------------------------

function decimalParse(value) {
	if (typeof value === "number") {
		if (Math.abs(value) > Number.MAX_SAFE_INTEGER) throw new Gap(DecimalText.Invalid);
		value = String(value);
	}
	const found = /^([+-]?)([0-9]+)(?:\.([0-9]*))?(?:[eE]([+-]?[0-9]+))?$/.exec(value);
	if (!found || found[0] !== value) throw new Gap(DecimalText.Invalid);
	const [, sign, whole, fraction = "", exponent = "0"] = found;
	const shift = Number(exponent) - fraction.length;
	if (!Number.isSafeInteger(shift) || Math.abs(shift) > 100000) throw new Gap(DecimalText.Invalid);
	let digits = (whole + fraction).replace(/^0+/, "") || "0";
	if (digits === "0") return "0";
	if (shift >= 0) digits += "0".repeat(shift);
	else {
		digits = digits.padStart(1 - shift, "0");
		digits = (digits.slice(0, shift) + "." + digits.slice(shift)).replace(/0+$/, "").replace(/\.$/, "");
	}
	return (sign === "-" ? "-" : "") + digits;
}

function instantParse(value) {
	const { date, time, zone } = dateParts(value);
	let [year, month, day] = date.split("-").map(Number);
	const [seconds = "00", fraction = ""] = time.slice(6).split(".");
	if (fraction.replace(/0+$/, "").length > 9) throw new Gap(InstantOffset.Invalid);
	const offset = zone === "Z" ? 0 : (+zone.slice(1, 3) * 60 + +zone.slice(4)) * (zone[0] === "-" ? -1 : 1);
	const total = +time.slice(0, 2) * 60 + +time.slice(3, 5) - offset;
	day += Math.floor(total / 1440);
	const minutes = ((total % 1440) + 1440) % 1440;
	while (day < 1) {
		if (--month === 0) { year--; month = 12; }
		if (year < 1) throw new Gap(InstantOffset.Invalid);
		day += daysInMonth(year, month);
	}
	while (day > daysInMonth(year, month)) {
		day -= daysInMonth(year, month);
		if (++month === 13) { year++; month = 1; }
		if (year > 9999) throw new Gap(InstantOffset.Invalid);
	}
	const pad = (number, width = 2) => String(number).padStart(width, "0");
	const tail = fraction.replace(/0+$/, "");
	const result = `${pad(year, 4)}-${pad(month)}-${pad(day)}T${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}:${seconds || "00"}${tail ? "." + tail : ""}Z`;
	return zone === "Z" ? result : lossy(result, InstantOffset.Discarded);
}

function datePatternRegex(pattern) {
	const tokens = pattern.split(/(YYYY|MM|DD)/);
	if (tokens.filter(token => ["YYYY", "MM", "DD"].includes(token)).sort().join() !== "DD,MM,YYYY") throw new Gap(DatePattern.Invalid);
	return new RegExp("^" + tokens.map(token => ["YYYY", "MM", "DD"].includes(token) ? `(?<${token}>[0-9]{${token === "YYYY" ? 4 : 2}})` : token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("") + "$(?![\\s\\S])");
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

export const DateOnly = where(value => dateParts(value)?.time === null, "DateOnly");
export const Timed = where(value => Boolean(dateParts(value)?.time), "Timed");
export const Zoned = where(value => Boolean(dateParts(value)?.zone), "Zoned");
export const Positive = where(value => typeof value === "number" && value > 0, "Positive");
export const Nonempty = where(value => value != null && (typeof value === "string" || Array.isArray(value) ? value.length > 0 : typeof value === "object" && Object.keys(value).length > 0), "Nonempty");
export const Location = slot("Location", Text);
export const Origin = slot("Origin", { strand: Text, seq: Int, pointer: Text });
export const parts = transform("parts", dateParts, { inputs: [DateTime], output: { date: Text, time: nullable(Text), zone: nullable(Text) }, examples: [{ input: ["2026-11-10"], output: { date: "2026-11-10", time: null, zone: null } }] });
export const DecimalText = codec("DecimalText", { source: either(Text, Num), canonical: Decimal, parse: decimalParse, format: value => value, gaps: ["Invalid"], examples: [{ input: "1.2500e3", output: "1250", direction: "forward" }] });
export const InstantOffset = codec("InstantOffset", { source: refine(DateTime, Zoned), canonical: Instant, parse: instantParse, format: value => value, gaps: ["Invalid"], losses: ["Discarded"], examples: [{ input: "2026-11-10T14:00+13:00", output: "2026-11-10T01:00:00Z", direction: "forward" }] });
export const DatePattern = codec("DatePattern", {
	source: Text, canonical: refine(DateTime, DateOnly), params: { Pattern: Text }, gaps: ["Invalid"],
	parse(pattern, value) {
		const found = datePatternRegex(pattern).exec(value);
		if (!found) throw new Gap(DatePattern.Invalid);
		const result = `${found.groups.YYYY}-${found.groups.MM}-${found.groups.DD}`;
		if (!dateParts(result)) throw new Gap(DatePattern.Invalid);
		return result;
	},
	format(pattern, value) {
		datePatternRegex(pattern);
		return pattern.replace("YYYY", value.slice(0, 4)).replace("MM", value.slice(5, 7)).replace("DD", value.slice(8, 10));
	},
	examples: [{ arguments: ["YYYYMMDD"], input: "20260915", output: "2026-09-15" }],
});

// EOF
