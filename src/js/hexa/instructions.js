/** Module: hexa.instructions — Inert instruction records, distinct from plain data. */

import { ABSENT, clone, freeze, isPlain, normalise, validateData } from "./core.js";
import { asStage } from "./stage.js";

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

/** A host operation with detached, immutable data arguments. */
export class Effect {
	constructor(operation, args) {
		validateData(args);
		Object.assign(this, { operation, arguments: freeze(normalise(clone(args))) });
		Object.freeze(this);
	}
}
/** A ledger entry to be validated and recorded by the runtime. */
export class Report {
	constructor(kind, { target, reason, source = ABSENT, path = ABSENT, fatal = false }) {
		Object.assign(this, { kind, target, reason, source, path, fatal });
		Object.freeze(this);
	}
}
/** Request a snapshot of the current strand's ledger. */
export class LedgerRead {}
/** Concurrent items, with a shallow copy of their list or struct container. */
export class Join {
	constructor(items) {
		if (!Array.isArray(items) && !isPlain(items)) throw new TypeError("join/race requires list or struct");
		this.items = Array.isArray(items) ? items.slice() : { ...items };
		Object.freeze(this);
	}
}
/** Concurrent alternatives whose first completion determines the outcome. */
export class Race extends Join {}
/** A child stage and its inputs, to be joined by its owner. */
export class Fork {
	constructor(stage, inputs) { Object.assign(this, { stage: asStage(stage), inputs }); Object.freeze(this); }
}
/** A stage to run over values with bounded concurrency. */
export class Parallel {
	constructor(stage, values) { Object.assign(this, { stage, values }); Object.freeze(this); }
}

// -----------------------------------------------------------------------------
//
// HIGH-LEVEL API
//
// -----------------------------------------------------------------------------

/** Describe a value-free ledger report without executing it. */
export function report(kind, details) {
	if (typeof details.target === "string") throw new TypeError("target must be program entity");
	return new Report(kind, details);
}
/** Request a detached snapshot of the current strand's ledger. */
export function ledger() { return new LedgerRead(); }
/** Describe concurrent work retaining the input's list or struct shape. */
export function join(items) { return new Join(items); }
/** Describe alternatives returning the first completed result. */
export function race(items) { return new Race(items); }
/** Describe a child stage owned by the current strand. */
export function fork(stage, ...inputs) { return new Fork(stage, inputs); }

// EOF
