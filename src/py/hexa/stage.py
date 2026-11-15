"""Module: hexa.stage — Wrap generators as named, traced and contract-checked stages."""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from .core import REGISTRY, Checks, Failure, Issue, Runtime, register
from .shape import check, compileShape, errors

# -----------------------------------------------------------------------------
#
# DEFINITIONS
#
# -----------------------------------------------------------------------------

__all__ = ["Stage", "asStage", "stage"]


@dataclass(frozen=True, slots=True)
class Stage:
	"""Wrap a generator with explicit naming, tracing and optional contracts."""

	function: object
	combinator: str
	name: str | None = None
	input: object = None
	output: object = None
	explicit: bool = False

	def __set_name__(self, owner, name):
		self.bind(name)

	def bind(self, name):
		"""Name this stage once and register it under `name`."""
		if self.name is not None and self.name != name:
			raise Failure(Failure.Defect, Runtime.StageRenamed)
		object.__setattr__(self, "name", name)
		register(name, self)

	def encoded(self):
		if not self.name or REGISTRY.get(self.name) != self:
			raise TypeError("stages must be named to encode")
		return "$" + self.name

	def __call__(self, env, *inputs):
		strand = getattr(env, "strand", None)
		labels = [self.name] if self.name else []
		if self.combinator and self.combinator != self.name:
			labels.append(self.combinator)
		if strand:
			strand.stack.extend(labels)
		try:
			boundary = strand is not None and len(strand.stack) == len(labels)
			checks = env.policy.get("contracts", Checks.Boundary)
			if self.explicit and self.input is not None and (boundary or checks is not Checks.Off):
				check(list(inputs), self.input, Issue.Mismatch)
			iterator = self.function(env, *inputs)
			if not inspect.isgenerator(iterator):
				if inspect.iscoroutine(iterator):
					iterator.close()
				raise Failure(Failure.Defect, Issue.Mismatch)
			result = yield from iterator
			if self.explicit and self.output is not None and checks is not Checks.Off:
				if errors(result, self.output):
					raise Failure(Failure.Defect, Issue.Mismatch)
			return result
		except Failure as failure:
			if not failure.trace and strand:
				failure.trace = list(strand.stack)
			raise
		except Exception as error:
			failure = Failure(Failure.Defect, Runtime.CloseYielded if strand and strand.closing and isinstance(error, RuntimeError) else Issue.Mismatch)
			if strand:
				failure.trace = list(strand.stack)
			raise failure from error
		finally:
			if strand and labels:
				del strand.stack[-len(labels):]


# -----------------------------------------------------------------------------
#
# HIGH-LEVEL API
#
# -----------------------------------------------------------------------------

def asStage(value):
	"""Return `value` as a stage; a generator function keeps its name but is not registered."""
	if isinstance(value, Stage):
		return value
	if not inspect.isgeneratorfunction(value):
		raise TypeError("stages must be generators; use lift for pure functions")
	return Stage(value, "", value.__name__)


def stage(*arguments, input=None, output=None):
	"""Name a computed stage or attach explicit call contracts."""
	name = arguments[0] if arguments and isinstance(arguments[0], str) else None
	values = arguments[1:] if name is not None else arguments
	if not values:
		return lambda value: stage(*arguments, value, input=input, output=output)
	if len(values) != 1:
		raise TypeError("stage takes one generator or stage")
	source = asStage(values[0])
	result = Stage(source.function, source.combinator, source.name,
		compileShape(input) if input is not None else source.input,
		compileShape(output) if output is not None else source.output,
		source.explicit or input is not None or output is not None)
	if name is not None:
		result.bind(name)
	return result

# EOF
