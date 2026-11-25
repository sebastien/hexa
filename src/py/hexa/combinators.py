"""Module: hexa.combinators — Declare named generator stages and failure policies."""

from __future__ import annotations

import inspect
from copy import deepcopy
from dataclasses import dataclass

from .core import ABSENT, DEFAULT_DELAY, FAILURE_MEMBERS, Failure, Gap, ImmutableMeta, Issue, Ledger, Namespace, Profile, backoff, constructor, failureData, freeze, member, register, retryable, thaw, validateData, validateMemberNames
from .profile import RequestFormat, admitData
from .instructions import LedgerRead, Parallel, Report
from .selector import matches
from .shape import Slot, capture, check, compileShape, definiteReferences, errors, mergeCaptures, references, validateShape
from .stage import Stage, asStage, stage
from .template import compileTemplate, expand, outputShape, validateTemplateReferences
from .vocabulary import Location, Origin

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = [
	"stages", "stage", "pipe", "lift", "project", "guard", "select", "each", "parallel",
	"choose", "when", "otherwise", "admit", "fold", "tap", "bound", "recover", "retry",
	"always", "collect", "validate", "plan", "Policy", "backoff"
]

# NOTE: Policies only ever handle these kinds; `on` narrows the default further.
RECOVERABLE_KINDS = (Failure.Shape, Failure.Gap, Failure.Effect)
DEFAULT_RECOVER = (Failure.Shape, Failure.Gap)


@dataclass(frozen=True, slots=True)
class PolicyValue:
	"""A parameterised policy: `Policy.Substitute` carries a value, `Policy.Fallback` a stage."""

	kind: object
	value: object

	def encoded(self):
		if self.kind is Policy.Substitute:
			return {"$": self.kind.name, "value": thaw(self.value)}
		if not self.value.name:
			raise TypeError("fallback stages must be named to encode")
		return {"$": self.kind.name, "stage": self.value.name}


def substitute(value):
	validateData(value)
	return PolicyValue(Policy.Substitute, freeze(deepcopy(value)))


def fallback(stage):
	return PolicyValue(Policy.Fallback, asStage(stage))


Policy = register("Policy", Namespace("Policy", {
	"Abort": member("Policy.Abort"),
	"Drop": member("Policy.Drop"),
	"Substitute": constructor("Policy.Substitute", substitute),
	"Fallback": constructor("Policy.Fallback", fallback),
}))


class StagesMeta(ImmutableMeta):
	"""Bind and freeze the stages declared in a `stages` class body."""

	def __new__(metaclass, name, bases, members):
		if bases:
			validateMemberNames(members)
			for key, value in tuple(members.items()):
				if key.startswith("__"):
					continue
				if not isinstance(value, Stage) and not inspect.isgeneratorfunction(value):
					raise TypeError("stage namespaces contain stages only")
				value = asStage(value)
				value.bind(key)
				members[key] = value
		return super().__new__(metaclass, name, bases, members)

	def __call__(cls, *args, **kwargs):
		raise TypeError("stage namespaces cannot be instantiated")


class stages(metaclass=StagesMeta):
	"""Declare a frozen class namespace containing only stages."""


@dataclass(frozen=True, slots=True)
class Branch:
	"""A compiled routing shape and its stage; `None` is the catch-all shape."""

	shape: object
	stage: Stage


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def isOccurrence(value):
	"""Tell whether `value` is a located occurrence produced by `select`."""
	return isinstance(value, dict) and "value" in value and "location" in value


def reportTarget(env, explicit=None):
	"""Resolve an explicit report target or the nearest named enclosing stage."""
	if explicit is not None:
		if not isinstance(explicit, (Stage, Namespace, Slot)):
			raise TypeError("target must be a program entity")
		return explicit
	for name in reversed(env.strand.stack):
		if name and name[0].isupper():
			return name
	# Unnamed stages are still inspectable by their canonical combinator name.
	return env.strand.stack[-1]


def validateOptions(options):
	"""Reject unknown policy options and undeclared failure kinds."""
	if options.keys() - {"on", "target", "reason"}:
		raise TypeError("unknown policy option")
	if "on" in options and any(kind not in FAILURE_MEMBERS for kind in options["on"]):
		raise TypeError("on must contain Failure members")


def applyPolicy(env, source, inputs, policy, options, *, allowDrop=False):
	"""Run `source`, applying `policy` to its recoverable failures."""
	try:
		return (yield from source(env, *inputs))
	except Failure as failure:
		if failure.kind not in RECOVERABLE_KINDS or failure.kind not in options.get("on", DEFAULT_RECOVER) or failure.fatal or failure.exhausted:
			raise
		reason = options.get("reason", failure.code)
		reason = reason(failure) if callable(reason) else reason
		fields = {"target": reportTarget(env, options.get("target")), "reason": reason}
		location = failure.location
		if location is None and inputs and isOccurrence(inputs[0]):
			location = inputs[0]["location"]
		if location is not None:
			fields["source"] = location
		if failure.path is not None:
			fields["path"] = failure.path
		if policy is Policy.Abort:
			fields["fatal"] = True
		yield Report(Ledger.Gap, **fields)
		if policy is Policy.Abort:
			failure.fatal, failure.code = True, reason
			raise
		if policy is Policy.Drop and allowDrop:
			return ABSENT
		if isinstance(policy, PolicyValue):
			if policy.kind is Policy.Substitute:
				value = thaw(policy.value)
				if source.output is not None and errors(value, source.output):
					raise Failure(Failure.Defect, Issue.Mismatch)
				return value
			return (yield from policy.value(env, *inputs))
		raise TypeError("invalid failure policy")


def requestOptions(env, format):
	"""Return the options of `format` under `env.policy.profile`, refusing an unpermitted format."""
	if not isinstance(format, RequestFormat):
		raise TypeError("request stages take a request format")
	definition = env.policy.get("profile")
	options = definition.options(format) if definition is not None else None
	if options is None:
		raise Failure(Failure.Contract, Profile.Forbidden)
	return definition, options


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

def lift(function):
	"""Wrap a pure function as a stage without introducing an effect."""
	def execute(env, *inputs):
		return (yield function(*inputs))
	return Stage(execute, "lift")


def pipe(*sources):
	"""Pass each stage's output to the next stage."""
	sources = tuple(asStage(item) for item in sources)
	if not sources:
		raise TypeError("pipe requires stages")
	def execute(env, *inputs):
		value = yield from sources[0](env, *inputs)
		for source in sources[1:]:
			value = yield from source(env, value)
		return value
	return Stage(execute, "pipe", input=sources[0].input, output=sources[-1].output)


def guard(shape, code):
	"""Assert a fixed or environment-derived precondition."""
	def execute(env, value):
		return (yield check(value, shape(env) if callable(shape) else shape, code))
	return Stage(execute, "guard", output=None if callable(shape) else compileShape(shape))


def select(selector):
	"""Enumerate occurrences and attach known effect provenance."""
	def execute(env, value):
		occurrences = matches(value, selector)
		for occurrence in occurrences:
			origin = env.strand.origin(value, occurrence["location"])
			if origin is not None:
				occurrence["origin"] = origin
		return (yield occurrences)
	return Stage(execute, "select")


def project(shape, template, *, outer=(), holes=Policy.Drop):
	"""Capture a value or occurrence and report missing template fields."""
	shape, template = compileShape(shape), compileTemplate(template)
	validateShape(shape)
	if holes not in (Policy.Drop, Policy.Abort):
		raise TypeError("holes must be Policy.Drop or Policy.Abort")
	unknown = references(template) - references(shape) - set(outer) - {Location, Origin}
	if unknown:
		raise TypeError("template references slots not supplied by shape or outer")
	validateTemplateReferences(template, references(shape) - definiteReferences(shape) - set(outer))
	def execute(env, value):
		occurrence = value if isOccurrence(value) else {"value": value, "location": ""}
		try:
			captures = capture(occurrence["value"], shape)
			if "captures" in occurrence:
				mergeCaptures(captures, occurrence["captures"])
			captures[Location] = occurrence["location"]
			if "origin" in occurrence:
				captures[Origin] = occurrence["origin"]
			result, missing = expand(template, captures)
			for loss in captures.losses:
				yield Report(Ledger.Loss, **loss, source=occurrence["location"])
			for hole in missing:
				if holes is Policy.Abort:
					raise Gap(hole["reason"], hole["path"], occurrence["location"])
				yield Report(Ledger.Gap, target=reportTarget(env), reason=hole["reason"], path=hole["path"], source=occurrence["location"])
			return result
		except Failure as failure:
			if failure.location is None:
				failure.location = occurrence["location"]
			raise
	return Stage(execute, "project", input=shape, output=outputShape(template))


def each(source, policy=Policy.Abort, **options):
	"""Process items in source order, applying a policy independently."""
	validateOptions(options)
	source = asStage(source)
	def execute(env, values):
		result = []
		for value in values:
			output = yield from applyPolicy(env, source, (value,), policy, options, allowDrop=True)
			if output is not ABSENT:
				result.append(output)
		return result
	return Stage(execute, "each")


def parallel(source, policy=Policy.Abort, **options):
	"""Process independent items with bounded concurrent strands."""
	validateOptions(options)
	source = asStage(source)
	def item(env, value):
		return (yield from applyPolicy(env, source, (value,), policy, options, allowDrop=True))
	def execute(env, values):
		result = yield Parallel(Stage(item, ""), values)
		return [value for value in result if value is not ABSENT]
	return Stage(execute, "parallel")


def when(shape, source):
	"""Declare a capture-free routing branch."""
	shape = compileShape(shape)
	validateShape(shape)
	if references(shape):
		raise TypeError("routing shapes cannot contain slots or codecs")
	return Branch(shape, asStage(source))


def otherwise(source):
	"""Declare the final catch-all routing branch."""
	return Branch(None, asStage(source))


def choose(*branches):
	"""Run the first matching branch without capturing routing values."""
	if not branches or any(branch.shape is None for branch in branches[:-1]):
		raise TypeError("otherwise must be the last branch")
	def execute(env, value):
		for branch in branches:
			if branch.shape is None or not errors(value, branch.shape):
				return (yield from branch.stage(env, value))
		raise Failure(Failure.Shape, Issue.Mismatch)
	return Stage(execute, "choose")


def recover(source, policy, **options):
	"""Apply a policy to recoverable failures from one stage."""
	validateOptions(options)
	if policy is Policy.Drop:
		raise TypeError("Policy.Drop is valid only in each or parallel")
	source = asStage(source)
	def execute(env, *inputs):
		return (yield from applyPolicy(env, source, inputs, policy, options))
	return Stage(execute, "recover", input=source.input, output=source.output)


def collect(source):
	"""Return a stage's value with the ledger entries it reported."""
	source = asStage(source)
	def execute(env, *inputs):
		before = yield LedgerRead()
		value = yield from source(env, *inputs)
		after = yield LedgerRead()
		return {"value": value, "ledger": [entry for entry in after if entry not in before]}
	return Stage(execute, "collect", input=source.input)


def tap(source):
	"""Run a stage on a detached copy and pass the input through."""
	source = asStage(source)
	def execute(env, value):
		yield from source(env, deepcopy(value))
		return value
	return Stage(execute, "tap")


def fold(create, source):
	"""Create a run-local accumulator and process items sequentially."""
	source = asStage(source)
	def execute(env, values):
		accumulator = create(env)
		for value in values:
			accumulator = yield from source(env, accumulator, value)
		return accumulator
	return Stage(execute, "fold")


def admit(*rules):
	"""Partition items by the first failing rule, reporting each rejection."""
	def execute(env, values):
		admitted, rejected = [], []
		for value in values:
			for rule, reason in rules:
				if not (rule(value) if callable(rule) else not errors(value, rule)):
					rejected.append({"value": value, "reason": str(reason)})
					yield Report(Ledger.Gap, target=reportTarget(env), reason=reason)
					break
			else:
				admitted.append(value)
		return {"Admitted": admitted, "Rejected": rejected}
	return Stage(execute, "admit")


def bound(key, measure):
	"""Fail when a measured value exceeds its host policy budget."""
	def execute(env, value):
		if measure(value) > env.policy[key]:
			raise Failure(Failure.Budget, Issue.Constraint)
		return (yield value)
	return Stage(execute, "bound")


def retry(source, *, attempts=None, delay=None):
	"""Rerun a stage for transient effect failures without nested retries."""
	source = asStage(source)
	def execute(env, *inputs):
		strand = env.strand
		defaults = env.policy.get("retry", {})
		limit = attempts if attempts is not None else defaults.get("attempts", 1)
		wait = delay if delay is not None else defaults.get("delay", DEFAULT_DELAY)
		if type(limit) is not int or limit < 1 or limit > env.policy["maxAttempts"]:
			raise Failure(Failure.Budget, Issue.Constraint)
		previous = strand.retryDepth, strand.attempt
		strand.retryDepth += 1
		try:
			for attempt in range(1, limit + 1):
				strand.attempt = attempt
				try:
					return (yield from source(env, *inputs))
				except Failure as failure:
					if not retryable(failure):
						raise
					if attempt == limit:
						failure.exhausted = True
						raise
					yield env.DelayWait(wait(attempt))
		finally:
			strand.retryDepth, strand.attempt = previous
	return Stage(execute, "retry", input=source.input, output=source.output)


def validate(format):
	"""Admit caller data as a request of `format` under the environment's profile."""
	if not isinstance(format, RequestFormat):
		raise TypeError("validate takes a request format")
	def execute(env, value):
		definition, options = requestOptions(env, format)
		admitData(value, definition)
		if not isinstance(value, dict):
			raise Failure(Failure.Contract, Issue.Mismatch, [])
		if "version" not in value:
			raise Failure(Failure.Contract, Issue.Missing, ["version"])
		if type(value["version"]) is not int or value["version"] not in options["versions"]:
			raise Failure(Failure.Contract, Profile.Forbidden, ["version"])
		result = format.validate(options, deepcopy(value), thaw(env.vocabulary.get(format.name, {})))
		validateData(result)
		return (yield result)
	return Stage(execute, "validate")


def plan(format):
	"""Compile a validated request of `format` into a plan, without effects."""
	if not isinstance(format, RequestFormat):
		raise TypeError("plan takes a request format")
	def execute(env, request):
		_, options = requestOptions(env, format)
		compiled = format.compile(options, deepcopy(request), thaw(env.vocabulary.get(format.name, {})))
		allowed = env.policy.get("allow")
		if allowed is not None and any(item not in allowed for item in compiled["allow"]):
			raise Failure(Failure.Contract, Profile.Forbidden)
		gaps = []
		for gap in compiled["gaps"]:
			if gap["code"] not in format.gaps:
				raise Failure(Failure.Defect, Issue.Unexpected)
			gaps.append({"code": str(gap["code"]), "path": list(gap["path"])})
		budgets = {key: value for key, value in env.policy.items() if key.startswith("max") and type(value) is int}
		result = {"request": deepcopy(request), "steps": compiled["steps"], "allow": compiled["allow"], "budgets": budgets, "gaps": gaps}
		validateData(result)
		return (yield result)
	return Stage(execute, "plan")


def always(source, cleanup):
	"""Run cleanup after success or failure, but never after cancellation."""
	source, cleanup = asStage(source), asStage(cleanup)
	def execute(env, *inputs):
		failure = None
		try:
			value = yield from source(env, *inputs)
			state = {"value": value}
		except Failure as caught:
			if caught.kind is Failure.Cancel:
				raise
			failure = caught
			state = {"failure": failureData(caught)}
		state["ledger"] = yield LedgerRead()
		try:
			yield from cleanup(env, state)
		except Failure as caught:
			if failure is None:
				raise
			failure.cleanup = caught
		if failure:
			raise failure
		return value
	return Stage(execute, "always", input=source.input, output=source.output)

# EOF
