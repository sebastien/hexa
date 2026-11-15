"""Module: hexa.runtime — Drive inert effects with structured strands and replay."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from string.templatelib import Template
from urllib.parse import quote

from .core import (
	ABSENT, DEFAULT_DELAY, LIMITS, Checks, Coverage, Failure, Issue, Ledger, Member, Runtime,
	entityName, failureData, failureFromData, freeze, jsonEqual, normalise, pointer, retryable, thaw, unescapeSegment, validateData,
)
from .instructions import Effect, Fork, Join, LedgerRead, Parallel, Race, Report, effect, fork, join, ledger, race, report
from .profile import ProfileDefinition
from .shape import typeMatches
from .stage import asStage

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = [
	"run", "report", "ledger", "join", "race", "fork", "inert", "p", "Ledger", "Coverage",
	"Checks", "Runtime"
]

DEFAULT_POLICY = {"contracts": Checks.Boundary, "maxGaps": 1000, "maxConcurrency": 4, "maxAttempts": 3, **LIMITS, "retry": {"default": False, "attempts": 1}}


@dataclass(frozen=True, slots=True)
class Environment:
	"""Frozen stage-facing authority plus inert operation constructors.

	`strand` is the runtime's handle on the executing strand; stages never use it.
	"""

	root: object
	policy: Mapping
	vocabulary: Mapping
	scope: object
	strand: object = None

	def __getattr__(self, name):
		if name.startswith("_") or not name[:1].isupper():
			raise AttributeError(name)
		def operation(*arguments):
			return effect(name, list(arguments))
		return operation


@dataclass(slots=True, eq=False)
class Handle:
	owner: object
	strand: object
	task: object
	joined: bool = False


@dataclass(slots=True)
class Outcome:
	value: object = None
	error: BaseException | None = None
	order: int = 0


# -----------------------------------------------------------------------------
#
# UTILITIES
#
# -----------------------------------------------------------------------------

def environmentSnapshot(environment):
	"""Validate a host environment mapping into a frozen `Environment` and its operations."""
	if not isinstance(environment, Mapping):
		raise TypeError("environment must be a mapping")
	if any(type(key) is not str for key in environment) or not isinstance(environment.get("policy", {}), Mapping) or not isinstance(environment.get("vocabulary", {}), Mapping):
		raise Failure(Failure.Contract, Issue.Mismatch)
	root = deepcopy(environment.get("root", {}))
	validateData(root)
	policy = dict(DEFAULT_POLICY)
	policy.update(environment.get("policy", {}))
	policy["retry"] = {**DEFAULT_POLICY["retry"], **policy.get("retry", {})}
	for key in ("maxGaps", "maxConcurrency", "maxAttempts", "maxDepth", "maxNodes", "maxBytes"):
		if type(policy[key]) is not int or policy[key] < (0 if key == "maxGaps" else 1):
			raise Failure(Failure.Contract, Issue.Constraint)
	if "profile" in policy and not isinstance(policy["profile"], ProfileDefinition):
		raise Failure(Failure.Contract, Issue.Mismatch)
	if "allow" in policy and (not isinstance(policy["allow"], (list, tuple)) or any(type(item) is not str for item in policy["allow"])):
		raise Failure(Failure.Contract, Issue.Mismatch)
	if policy["contracts"] not in (Checks.Boundary, Checks.Off):
		raise Failure(Failure.Contract, Runtime.MemberUnknown)
	if type(policy["retry"]["attempts"]) is not int or not 1 <= policy["retry"]["attempts"] <= policy["maxAttempts"]:
		raise Failure(Failure.Contract, Issue.Constraint)
	if type(policy["retry"]["default"]) is not bool or ("delay" in policy["retry"] and not callable(policy["retry"]["delay"])):
		raise Failure(Failure.Contract, Issue.Mismatch)
	scopeData = deepcopy(environment.get("scope", {}))
	validateData(scopeData)
	operations = {key: value for key, value in environment.items() if callable(value) and key[:1].isupper()}
	data = Environment(freeze(root), freeze(policy), freeze(dict(environment.get("vocabulary", {}))), freeze(scopeData))
	return data, operations


def replayIndex(replay):
	"""Index effect positions and race decisions in separate identity domains."""
	if replay is None:
		return None
	replay = deepcopy(replay)
	validateData(replay)
	if not isinstance(replay, list):
		raise Failure(Failure.Contract, Runtime.ReplayMismatch)
	result = {}
	for entry in replay:
		if not isinstance(entry, dict) or type(entry.get("strand")) is not str:
			raise Failure(Failure.Contract, Runtime.ReplayMismatch)
		if "instruction" in entry:
			keys = entry.get("keys")
			if entry["instruction"] != "race" or type(entry.get("ordinal")) is not int or entry["ordinal"] < 0 or not isinstance(keys, list) or not keys:
				raise Failure(Failure.Contract, Runtime.ReplayMismatch)
			structKeys = all(type(key) is str and not key.startswith("@") for key in keys)
			listKeys = all(type(key) is int for key in keys) and keys == list(range(len(keys)))
			if not (structKeys or listKeys) or len(set(keys)) != len(keys):
				raise Failure(Failure.Contract, Runtime.ReplayMismatch)
			if ("winner" in entry) == ("cancelled" in entry) or ("cancelled" in entry and entry["cancelled"] is not True):
				raise Failure(Failure.Contract, Runtime.ReplayMismatch)
			if "winner" in entry and not any(jsonEqual(entry["winner"], key) for key in keys):
				raise Failure(Failure.Contract, Runtime.ReplayMismatch)
			if entry.keys() - {"index", "instruction", "strand", "ordinal", "keys", "winner", "cancelled"}:
				raise Failure(Failure.Contract, Runtime.ReplayMismatch)
			key = entry["strand"], "race", entry["ordinal"]
		else:
			if type(entry.get("seq")) is not int or entry["seq"] < 0 or sum(key in entry for key in ("result", "failure", "cancelled")) != 1:
				raise Failure(Failure.Contract, Runtime.ReplayMismatch)
			key = entry["strand"], "effect", entry["seq"]
		if key in result:
			raise Failure(Failure.Contract, Runtime.ReplayMismatch)
		result[key] = entry
	return result


def entryIdentity(entry):
	"""Identify a ledger entry regardless of whether it was reported as fatal."""
	return json.dumps({key: value for key, value in entry.items() if key != "fatal"}, sort_keys=True)


def mergeEntries(target, entries):
	# ponytail: bounded linear scans; index ledger identities if large ledgers become common.
	for entry in entries:
		identity = entryIdentity(entry)
		existing = next((item for item in target if entryIdentity(item) == identity), None)
		if existing is None:
			target.append(deepcopy(entry))
		elif entry.get("fatal"):
			existing["fatal"] = True


# -----------------------------------------------------------------------------
#
# OPERATIONS
#
# -----------------------------------------------------------------------------

class Run:
	"""Hold run-wide state shared by every strand: authority, budgets, journal and replay."""

	def __init__(self, environment):
		self.env, self.operations = environmentSnapshot(environment)
		self.policy = self.env.policy
		self.record = bool(environment.get("record"))
		self.hostSignal = environment.get("signal")
		if self.hostSignal is not None and not isinstance(self.hostSignal, asyncio.Event):
			raise TypeError("signal must be an asyncio.Event")
		self.replay = replayIndex(environment.get("replay"))
		self.usedReplay = set()
		self.journal = []
		self.origins = {}
		self.sequences = {}
		self.forks = {}
		self.races = {}
		self.active = set()
		self.ledgerKeys = set()
		self.completions = 0
		self.cancelTrace = []
		self.semaphore = asyncio.Semaphore(self.policy["maxConcurrency"])

	def validate(self, value, *, allowIndexMaps=False):
		"""Check boundary data against the policy's size budgets."""
		validateData(value, maxDepth=self.policy["maxDepth"], maxNodes=self.policy["maxNodes"], maxBytes=self.policy["maxBytes"], allowIndexMaps=allowIndexMaps)

	def nextIndex(self, counters, strand):
		"""Return and advance the per-strand counter in `counters`."""
		index = counters.get(strand.id, 0)
		counters[strand.id] = index + 1
		return index

	def appendJournal(self, entry):
		"""Append an effect completion or race decision without changing effect positions."""
		if self.record:
			entry["index"] = len(self.journal)
			self.journal.append(entry)

	def raceDecision(self, strand, keys):
		"""Allocate a race ordinal and validate its recorded participant keys on replay."""
		entry = {"instruction": "race", "strand": strand.id, "ordinal": self.nextIndex(self.races, strand), "keys": keys}
		recorded = None
		if self.replay is not None:
			key = strand.id, "race", entry["ordinal"]
			recorded = self.replay.get(key)
			if recorded is None or not jsonEqual(recorded["keys"], keys):
				raise Failure(Failure.Defect, Runtime.ReplayMismatch)
			self.usedReplay.add(key)
		return entry, recorded


class Strand:
	"""Own one strand's stack, ledger entries, handles and cancellation signal."""

	def __init__(self, run, *, parent=None, identifier=""):
		self.run, self.parent, self.id = run, parent, identifier
		self.entries, self.handles = [], []
		self.stack = list(parent.stack) if parent else []
		self.signal = asyncio.Event()
		self.closing = False
		self.retryDepth = parent.retryDepth if parent else 0
		self.attempt = parent.attempt if parent else 1
		self.env = Environment(run.env.root, run.env.policy, run.env.vocabulary, run.env.scope, self)

	def report(self, instruction):
		kind, reason = instruction.kind, instruction.reason
		if kind not in (Ledger.Gap, Ledger.Loss) or not isinstance(reason, Member):
			raise TypeError("ledger kind and reason must be declared members")
		entry = {"kind": kind.short, "target": entityName(instruction.target), "reason": str(reason)}
		for key in ("source", "path"):
			value = getattr(instruction, key)
			if value is not ABSENT:
				entry[key] = deepcopy(value)
		if instruction.fatal:
			entry["fatal"] = True
		validateData(entry)
		identity = entryIdentity(entry)
		if identity not in self.run.ledgerKeys:
			if len(self.run.ledgerKeys) >= self.run.policy["maxGaps"]:
				raise Failure(Failure.Budget, Issue.Constraint)
			self.run.ledgerKeys.add(identity)
		mergeEntries(self.entries, [entry])

	def mark(self, value, seq, path=()):
		if isinstance(value, (list, dict)):
			self.run.origins[id(value)] = (value, {"strand": self.id, "seq": seq, "pointer": pointer(path)})
			items = value.items() if isinstance(value, dict) else enumerate(value)
			for key, child in items:
				self.mark(child, seq, (*path, key))

	def origin(self, value, location):
		origin = None
		segments = location.split("/")[1:] if location else []
		for segment in [*segments, None]:
			mark = self.run.origins.get(id(value))
			if mark and mark[0] is value:
				origin = deepcopy(mark[1])
			if segment is None:
				break
			if origin:
				origin["pointer"] += "/" + segment
			key = unescapeSegment(segment)
			value = value[int(key)] if isinstance(value, list) else value[key]
		return origin

	async def drive(self, source, inputs):
		iterator = source(self.env, *inputs)
		if not inspect.isgenerator(iterator):
			if inspect.iscoroutine(iterator):
				iterator.close()
			raise Failure(Failure.Defect, Issue.Mismatch)
		send, error = None, None
		finished = False
		try:
			while True:
				if self.run.hostSignal and self.run.hostSignal.is_set():
					raise asyncio.CancelledError
				try:
					yielded = iterator.throw(error) if error else iterator.send(send)
				except StopIteration as stop:
					if any(not handle.joined for handle in self.handles):
						raise Failure(Failure.Defect, Runtime.StrandUnjoined)
					finished = True
					return stop.value
				send, error = None, None
				try:
					if inspect.isawaitable(yielded):
						if inspect.iscoroutine(yielded):
							yielded.close()
						raise Failure(Failure.Defect, Runtime.NativeAwaitable)
					send = await self.perform(yielded)
				except Failure as caught:
					if not caught.trace:
						caught.trace = list(self.stack)
					if caught.kind is Failure.Cancel:
						raise
					error = caught
				except Exception as caught:
					error = Failure(Failure.Defect, Issue.Mismatch)
					error.trace = list(self.stack)
					error.__cause__ = caught
		except asyncio.CancelledError:
			self.run.cancelTrace = list(self.stack)
			raise
		finally:
			if not finished:
				self.signal.set()
			self.closing = True
			try:
				iterator.close()
			except RuntimeError as caught:
				raise Failure(Failure.Defect, Runtime.CloseYielded) from caught
			finally:
				pending = [handle.task for handle in self.handles if not handle.joined]
				for task in pending:
					task.cancel()
				if pending:
					await asyncio.gather(*pending, return_exceptions=True)

	def replayResult(self, seq, operation, arguments, attempt):
		"""Answer an effect from the replay journal, which must match it exactly."""
		key = self.id, "effect", seq
		recorded = self.run.replay.get(key)
		if recorded is None or recorded.get("operation") != operation or not jsonEqual(recorded.get("arguments"), arguments) or recorded.get("attempt", 1) != attempt:
			raise Failure(Failure.Defect, Runtime.ReplayMismatch)
		self.run.usedReplay.add(key)
		if "cancelled" in recorded:
			raise asyncio.CancelledError
		if "failure" in recorded:
			raise failureFromData(recorded["failure"])
		return deepcopy(recorded["result"])

	async def callOperation(self, operation, arguments, attempt):
		"""Call the host operation (or a built-in one), classifying what it raises."""
		context = {"signal": self.signal, "attempt": attempt, "strand": self.id}
		function = self.run.operations.get(operation)
		async with self.run.semaphore:
			try:
				if function is not None:
					result = function(*arguments, context)
					return (await result) if inspect.isawaitable(result) else result
				if operation == "DelayWait":
					await asyncio.sleep(arguments[0] / 1000)
					return None
				if operation == "ProgressPublish":
					return None
				raise Failure(Failure.Effect, Runtime.MemberUnknown)
			except TimeoutError as caught:
				raise Failure(Failure.Cancel, Issue.Mismatch) from caught
			except Failure:
				raise
			except Exception as caught:
				classifier = self.run.operations.get("FailureClassify")
				classification = classifier(caught) if classifier else {"code": Issue.Mismatch, "transient": False}
				if inspect.isawaitable(classification):
					classification = await classification
				failure = Failure(Failure.Effect, classification["code"])
				failure.transient = bool(classification.get("transient"))
				raise failure from caught

	def validateResult(self, result):
		"""Check an effect result is bounded JSON; a host returning anything else is a defect."""
		if inspect.isawaitable(result):
			if inspect.iscoroutine(result):
				result.close()
			raise Failure(Failure.Defect, Runtime.NativeAwaitable)
		try:
			self.run.validate(result, allowIndexMaps=True)
		except Failure as failure:
			if failure.kind is Failure.Contract:
				raise Failure(Failure.Defect, failure.code) from failure
			raise
		return normalise(result)

	async def effectOnce(self, value, attempt):
		"""Perform one attempt of an effect, journalling its result, failure or cancellation."""
		operation, arguments = value.operation, thaw(value.arguments)
		self.run.validate(arguments, allowIndexMaps=True)
		arguments = normalise(arguments)
		seq = self.run.nextIndex(self.run.sequences, self)
		entry = {"strand": self.id, "seq": seq, "operation": operation, "arguments": deepcopy(arguments), "attempt": attempt}
		try:
			if self.run.replay is not None:
				result = self.replayResult(seq, operation, arguments, attempt)
			else:
				result = await self.callOperation(operation, arguments, attempt)
			result = self.validateResult(result)
			entry["result"] = deepcopy(result)
			self.mark(result, seq)
			return result
		except asyncio.CancelledError:
			self.signal.set()
			entry["cancelled"] = True
			raise
		except Failure as failure:
			entry["failure"] = failureData(failure)
			raise
		finally:
			self.run.appendJournal(entry)

	async def effect(self, value):
		defaults = self.run.policy["retry"]
		managed = defaults.get("default") and not self.retryDepth and value.operation != "DelayWait"
		if not managed:
			return await self.effectOnce(value, self.attempt if self.retryDepth else 1)
		limit = defaults["attempts"]
		for attempt in range(1, limit + 1):
			try:
				return await self.effectOnce(value, self.attempt if self.retryDepth else attempt)
			except Failure as failure:
				if not retryable(failure) or (limit == 1 and self.retryDepth):
					raise
				if attempt == limit:
					failure.exhausted = True
					raise
				delay = defaults.get("delay", DEFAULT_DELAY)(attempt)
				await self.effectOnce(Effect("DelayWait", [delay]), attempt)

	def child(self, identifier):
		if identifier in self.run.active:
			raise Failure(Failure.Defect, Runtime.StrandConflict)
		self.run.active.add(identifier)
		return Strand(self.run, parent=self, identifier=identifier)

	async def childRun(self, child, item, inputs=None, gate=None):
		try:
			if gate:
				async with gate:
					outcome = Outcome(await child.drive(item, inputs))
			else:
				outcome = Outcome(await child.drive(item, inputs) if inputs is not None else await child.perform(item))
		except asyncio.CancelledError as caught:
			child.signal.set()
			outcome = Outcome(error=caught)
		except Exception as caught:
			outcome = Outcome(error=caught)
		finally:
			self.run.active.discard(child.id)
		outcome.order = self.run.completions
		self.run.completions += 1
		return outcome

	def startFork(self, stage, inputs, gate=None):
		"""Start `stage` as a concurrent child strand owned by this strand."""
		child = self.child(self.id + f"/@{self.run.nextIndex(self.run.forks, self)}")
		handle = Handle(self, child, asyncio.create_task(self.childRun(child, stage, inputs, gate)))
		self.handles.append(handle)
		return handle

	async def joinItems(self, items):
		"""Run all items concurrently, returning results in the input's list or struct shape."""
		return await self.runItems(items, untilFirst=False)

	async def raceItems(self, items):
		"""Select the first completion, or the journal's explicit selection during replay."""
		return await self.runItems(items, untilFirst=True)

	async def runItems(self, items, untilFirst):
		if type(items) not in (dict, list) or (isinstance(items, dict) and any(not isinstance(key, str) or key.startswith("@") for key in items)):
			raise TypeError("join/race requires a list or struct with non-@ keys")
		if not items:
			if untilFirst:
				raise Failure(Failure.Contract, Issue.Missing)
			return {} if isinstance(items, dict) else []
		pairs = list(items.items()) if isinstance(items, dict) else list(enumerate(items))
		inputHandles = [item for _, item in pairs if isinstance(item, Handle)]
		if len(set(inputHandles)) != len(inputHandles) or any(handle.owner is not self or handle.joined for handle in inputHandles):
			raise Failure(Failure.Defect, Runtime.StrandUnjoined)
		keys = [key for key, _ in pairs]
		decision, recorded = self.run.raceDecision(self, keys) if untilFirst else (None, None)
		tasks, strands, handles = [], [], []
		winner, firstError = None, None
		async with asyncio.TaskGroup() as taskGroup:
			try:
				for key, item in pairs:
					if isinstance(item, Handle):
						tasks.append(item.task)
						strands.append(item.strand)
						handles.append(item)
					else:
						child = self.child(self.id + pointer([key]))
						strands.append(child)
						tasks.append(taskGroup.create_task(self.childRun(child, item)))
				if self.run.replay is not None:
					# Losers must consume their recorded work, including reports and cancellation.
					# Nested joins must drain too: early replay cancellation can erase prior work.
					await asyncio.gather(*tasks)
					if untilFirst:
						if "winner" in recorded:
							winner = keys.index(recorded["winner"])
							decision["winner"] = keys[winner]
							firstError = tasks[winner].result().error
						else:
							decision["cancelled"] = True
							firstError = asyncio.CancelledError()
						self.run.appendJournal(decision)
					else:
						firstError = next((outcome.error for outcome in sorted((task.result() for task in tasks), key=lambda outcome: outcome.order) if outcome.error), None)
				else:
					pending = set(tasks)
					while pending:
						done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
						for task in sorted(done, key=lambda task: task.result().order):
							outcome = task.result()
							if untilFirst:
								winner = tasks.index(task)
								decision["winner"] = keys[winner]
								self.run.appendJournal(decision)
							if outcome.error:
								firstError = outcome.error
								break
							if untilFirst:
								break
						if firstError or winner is not None:
							break
			except asyncio.CancelledError as caught:
				firstError = caught
				if untilFirst and "winner" not in decision and "cancelled" not in decision:
					decision["cancelled"] = True
					self.run.appendJournal(decision)
			finally:
				for task in tasks:
					if not task.done():
						task.cancel()
				if tasks:
					await asyncio.gather(*tasks, return_exceptions=True)
				for handle in handles:
					handle.joined = True
		# Merge after all children finish, in declaration order, even on failure.
		for child in strands:
			mergeEntries(self.entries, child.entries)
		for task in tasks:
			outcome = task.result()
			if isinstance(outcome.error, Failure) and outcome.error.code is Runtime.CloseYielded:
				if firstError is outcome.error:
					pass
				elif firstError and isinstance(firstError, Failure):
					firstError.cleanup = outcome.error
				else:
					firstError = outcome.error
			if isinstance(outcome.error, Failure) and outcome.error.code is Runtime.ReplayMismatch:
				firstError = outcome.error
		if firstError:
			raise firstError
		if untilFirst:
			if winner is None:
				raise asyncio.CancelledError
			key = pairs[winner][0]
			value = tasks[winner].result().value
			return {key: value} if isinstance(items, dict) else value
		values = [task.result().value for task in tasks]
		return dict(zip((key for key, _ in pairs), values)) if isinstance(items, dict) else values

	async def perform(self, value):
		"""Interpret one yielded value: effects and instructions act, anything else passes through."""
		if isinstance(value, Effect):
			return await self.effect(value)
		if isinstance(value, Report):
			return self.report(value)
		if isinstance(value, LedgerRead):
			return deepcopy(self.entries)
		if isinstance(value, Join):
			return await self.joinItems(value.items)
		if isinstance(value, Race):
			return await self.raceItems(value.items)
		if isinstance(value, Fork):
			return self.startFork(value.stage, value.inputs)
		if isinstance(value, Parallel):
			gate = asyncio.Semaphore(self.run.policy["maxConcurrency"])
			return await self.joinItems([self.startFork(value.stage, (item,), gate) for item in value.values])
		return value


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

def inert(env: Mapping) -> Environment:
	"""Return a frozen environment whose operations only describe effects."""
	return environmentSnapshot(env)[0]


def p(template: Template) -> str:
	"""Percent-encode each t-string interpolation as one safe path segment."""
	if not isinstance(template, Template):
		raise TypeError("p requires a template string, not str")
	parts = [template.strings[0]]
	for interpolation, literal in zip(template.interpolations, template.strings[1:]):
		value = interpolation.value
		if interpolation.conversion or interpolation.format_spec or not (typeMatches(value, "Text") or typeMatches(value, "Int")):
			raise Failure(Failure.Contract, Runtime.PathSegmentInvalid)
		value = str(int(value)) if type(value) in (int, float) else value
		if value in ("", ".", ".."):
			raise Failure(Failure.Contract, Runtime.PathSegmentInvalid)
		try:
			parts.extend((quote(value, safe=""), literal))
		except UnicodeError:
			raise Failure(Failure.Contract, Runtime.PathSegmentInvalid) from None
	return "".join(parts)


async def run(env: Mapping, stage: object, *inputs: object) -> object:
	"""Run a generator stage against a validated host environment mapping."""
	state = Run(env)
	strand = Strand(state)
	for value in inputs:
		state.validate(value)
	async def execute():
		return await strand.drive(asStage(stage), inputs)
	async def watch(task):
		await state.hostSignal.wait()
		task.cancel()
	watcher = None
	task = None
	if state.hostSignal:
		task = asyncio.create_task(execute())
		watcher = asyncio.create_task(watch(task))
	try:
		try:
			result = await task if task is not None else await execute()
			if state.replay is not None and state.usedReplay != state.replay.keys():
				raise Failure(Failure.Defect, Runtime.ReplayMismatch)
			return result
		except asyncio.CancelledError as caught:
			failure = Failure(Failure.Cancel, Issue.Mismatch)
			failure.trace = state.cancelTrace
			raise failure from caught
	except Failure as failure:
		failure.ledger = deepcopy(strand.entries)
		failure.journal = deepcopy(state.journal) if state.record else None
		raise
	finally:
		if watcher:
			watcher.cancel()
			await asyncio.gather(watcher, return_exceptions=True)

# EOF
