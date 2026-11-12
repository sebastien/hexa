"""Module: hexa.vocabulary — Standard constraints, slots, derivations and codecs."""

from __future__ import annotations

import re
from calendar import monthrange

from .codec import codec
from .core import MAX_INT, Gap
from .shape import DateTime, Decimal, Instant, Int, Num, Text, dateParts, either, lossy, nullable, slot, where
from .template import transform

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = [
	"DateOnly", "Timed", "Zoned", "Positive", "Nonempty", "Location", "Origin", "parts",
	"DecimalText", "InstantOffset", "DatePattern"
]

DECIMAL_SOURCE_RE = re.compile(r"([+-]?)([0-9]+)(?:\.([0-9]*))?(?:[eE]([+-]?[0-9]+))?")

# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def decimalParse(value):
	if type(value) in (int, float):
		if abs(value) > MAX_INT:
			raise Gap(DecimalText.Invalid)
		value = str(value)
	found = DECIMAL_SOURCE_RE.fullmatch(value)
	if not found:
		raise Gap(DecimalText.Invalid)
	sign, whole, fraction, exponent = found.groups()
	fraction = fraction or ""
	if exponent and len(exponent.lstrip("+-0")) > 6:
		raise Gap(DecimalText.Invalid)
	shift = int(exponent or 0) - len(fraction)
	if abs(shift) > 100000:
		raise Gap(DecimalText.Invalid)
	digits = (whole + fraction).lstrip("0") or "0"
	if digits == "0":
		return "0"
	if shift >= 0:
		result = digits + "0" * shift
	else:
		digits = digits.zfill(1 - shift)
		result = digits[:shift] + "." + digits[shift:]
		result = result.rstrip("0").rstrip(".")
	return ("-" if sign == "-" else "") + result


def instantParse(value):
	components = dateParts(value)
	year, month, day = map(int, components["date"].split("-"))
	timeText = components["time"]
	hour, minute = map(int, timeText[:5].split(":"))
	seconds = timeText[6:] if len(timeText) > 5 else "00"
	whole, _, fraction = seconds.partition(".")
	if len(fraction.rstrip("0")) > 9:
		raise Gap(InstantOffset.Invalid)
	zone = components["zone"]
	offset = 0 if zone == "Z" else (int(zone[1:3]) * 60 + int(zone[4:])) * (-1 if zone[0] == "-" else 1)
	dayShift, minutes = divmod(hour * 60 + minute - offset, 1440)
	day += dayShift
	while day < 1:
		month -= 1
		if month == 0:
			year, month = year - 1, 12
		if year < 1:
			raise Gap(InstantOffset.Invalid)
		day += monthrange(year, month)[1]
	while day > monthrange(year, month)[1]:
		day -= monthrange(year, month)[1]
		month += 1
		if month == 13:
			year, month = year + 1, 1
		if year > 9999:
			raise Gap(InstantOffset.Invalid)
	fraction = fraction.rstrip("0")
	result = f"{year:04d}-{month:02d}-{day:02d}T{minutes // 60:02d}:{minutes % 60:02d}:{whole}" + ("." + fraction if fraction else "") + "Z"
	return lossy(result, InstantOffset.Discarded) if zone != "Z" else result


def datePatternRegex(patternText):
	tokens = re.split(r"(YYYY|MM|DD)", patternText)
	if sorted(token for token in tokens if token in ("YYYY", "MM", "DD")) != ["DD", "MM", "YYYY"]:
		raise Gap(DatePattern.Invalid)
	return re.compile("".join(f"(?P<{token}>[0-9]{{{4 if token == 'YYYY' else 2}}})" if token in ("YYYY", "MM", "DD") else re.escape(token) for token in tokens))


def datePatternParse(patternText, value):
	found = datePatternRegex(patternText).fullmatch(value)
	if not found:
		raise Gap(DatePattern.Invalid)
	result = f"{found['YYYY']}-{found['MM']}-{found['DD']}"
	if dateParts(result) is None:
		raise Gap(DatePattern.Invalid)
	return result


def datePatternFormat(patternText, value, original=None):
	datePatternRegex(patternText)
	return patternText.replace("YYYY", value[:4]).replace("MM", value[5:7]).replace("DD", value[8:10])


def identityFormat(value, original=None):
	return value


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

DateOnly = where(lambda value: (components := dateParts(value)) is not None and components["time"] is None, "DateOnly")
Timed = where(lambda value: (components := dateParts(value)) is not None and components["time"] is not None, "Timed")
Zoned = where(lambda value: (components := dateParts(value)) is not None and components["zone"] is not None, "Zoned")
Positive = where(lambda value: type(value) in (int, float) and value > 0, "Positive")
Nonempty = where(lambda value: hasattr(value, "__len__") and len(value) > 0, "Nonempty")

Location = slot("Location", Text)
Origin = slot("Origin", {"strand": Text, "seq": Int, "pointer": Text})

parts = transform("parts", dateParts, inputs=[DateTime], output={"date": Text, "time": nullable(Text), "zone": nullable(Text)},
	examples=[{"input": ["2026-11-10"], "output": {"date": "2026-11-10", "time": None, "zone": None}}])

DecimalText = codec("DecimalText", source=either(Text, Num), canonical=Decimal, parse=decimalParse, format=identityFormat,
	gaps=["Invalid"], examples=[{"input": "1.2500e3", "output": "1250", "direction": "forward"}])
InstantOffset = codec("InstantOffset", source=DateTime & Zoned, canonical=Instant, parse=instantParse, format=identityFormat,
	gaps=["Invalid"], losses=["Discarded"], examples=[{"input": "2026-11-10T14:00+13:00", "output": "2026-11-10T01:00:00Z", "direction": "forward"}])
DatePattern = codec("DatePattern", source=Text, canonical=DateTime & DateOnly, params={"Pattern": Text}, parse=datePatternParse, format=datePatternFormat,
	gaps=["Invalid"], examples=[{"arguments": ["YYYYMMDD"], "input": "20260915", "output": "2026-09-15"}])

# EOF
