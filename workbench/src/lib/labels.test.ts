import { afterEach, describe, expect, it } from "vitest";
import { humanizePredicate } from "./format";
import { resetServedDictionary, servedIntentPurpose, setServedDictionary } from "./labels";

describe("served label dictionary (M25)", () => {
  afterEach(() => resetServedDictionary());

  it("layers curated override → engine-served → de-underscored fallback", () => {
    // before any snapshot: curated map wins, unknown de-underscores (behavior-preserving)
    expect(humanizePredicate("red_latency_p99")).toBe("latency p99"); // curated
    expect(humanizePredicate("brand_new_metric")).toBe("brand new metric"); // de-underscored

    setServedDictionary({
      predicates: { brand_new_metric: "brand new metric", red_latency_p99: "SERVED-should-not-win" },
      relations: {},
      intents: { some_new_intent: "do the new thing" },
    });

    // the curated label still WINS over the served one (exact current labels preserved)
    expect(humanizePredicate("red_latency_p99")).toBe("latency p99");
    // a NEW predicate now gets the served label instead of only the raw de-underscore (drift-prevention)
    expect(servedIntentPurpose("some_new_intent")).toBe("do the new thing");
    // an intent the engine didn't serve → undefined (caller falls back to its curated map / raw)
    expect(servedIntentPurpose("unheard_of")).toBeUndefined();
  });

  it("ignores a missing dictionary (older snapshot) — local labels keep working", () => {
    setServedDictionary(undefined);
    expect(servedIntentPurpose("anything")).toBeUndefined();
    expect(humanizePredicate("red_errors")).toBe("error rate"); // curated map unaffected
  });
});
