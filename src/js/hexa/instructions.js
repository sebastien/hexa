/** Module: hexa.instructions — Inert instruction records, distinct from plain data. */

import { ABSENT, clone, freeze, normalise, validateData } from "./core.js";
import { asStage } from "./stage.js";

// -----------------------------------------------------------------------------
//
// DEFINITIONS
//
// -----------------------------------------------------------------------------

export class Effect {
	constructor(operation, args) {
		validateData(args);
		Object.assign(this, { operation, arguments: freeze(normalise(clone(args))) });
		Object.freeze(this);
	}
}
export class Report {
	constructor(kind, { target, reason, source = ABSENT, path = ABSENT, fatal = false }) {
		Object.assign(this, { kind, target, reason, source, path, fatal });
		Object.freeze(this);
	}
}
export class LedgerRead {}
export class Join {
	constructor(items) { this.items = Array.isArray(items) ? items.slice() : { ...items }; Object.freeze(this); }
}
export class Race extends Join {}
export class Fork {
	constructor(stage, inputs) { Object.assign(this, { stage: asStage(stage), inputs }); Object.freeze(this); }
}
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
