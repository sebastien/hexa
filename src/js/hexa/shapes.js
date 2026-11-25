/** Module: hexa.shapes — Public façade for shapes, projections and vocabulary. */

export { Failure, Gap, Issue, Profile } from "./core.js";
export { Any, Text, Num, Int, Float, Bool, Null, Struct, List, DateTime, Instant, Decimal, Tuple, rest, __, loose, either, nullable, where, refine, via, optional, tolerant, gather, parsed, pattern, lossy, codes, slot, slots, errors, check, capture, evidence } from "./shape.js";
export { codec } from "./codec.js";
export { transform, derive, expand, reshape } from "./template.js";
export { matches } from "./selector.js";
export { DateOnly, Timed, Zoned, Positive, Nonempty, Location, Origin, parts, DecimalText, InstantOffset, DatePattern } from "./vocabulary.js";
export { profile, request } from "./profile.js";
export { SelectionRequest } from "./selection/request.js";
export { encode, decode, describe } from "./encoding.js";

// EOF
