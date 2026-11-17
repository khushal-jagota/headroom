# Measures

This area owns measure definitions, retained observations, formula results, manual receipts, and the eight measure routes. Dataset and Goal reads reuse these rules. Source execution belongs to the measure-source unit.

## Explicit depth budgets

`configuration.ts` defines fixed budgets. They do not follow the runtime stack size or the caller stack. The budgets come from measurements of the earlier Python server. Tests prove the fixed boundary.

Arithmetic uses a maximum weighted depth of **991**. Each nested expression edge adds **1**. Each reference to another measure adds **4**, including references from `sum_daily`. Goal actuals start with weight **2**. Ordinary batch reads and independent expressions start at zero. A weight of 991 is admitted. A weight of 992 returns `arithmetic_limit`.

These costs combine along a path. A shallow chain of 247 references costs 988. A chain of 248 references costs 992. Binary or conditional expressions around a reference add their edge weights to that same total. The budget does not restart at each measure. Cached results retain the existing snapshot semantics.

An ordinary batch read returns the limit result for all its roots if one root exceeds the budget. Goal actuals use independent evaluation entry points. A deep Goal root returns the limit result while another root can retain its value.

Formula admission uses a separate maximum weighted depth of **990**. Each nested expression edge adds **1**. Leaf validation has these additional costs:

| Leaf or validation | Weight |
| --- | ---: |
| Relative reference | 0 |
| Date, including fixed reference and conditional date | 1 |
| Number | 2 |
| Period enum for a daily sum | 2 |

Graph validation passes its current dependency depth into formula admission. Both consume the same parser budget. For example, graph depth 500 plus 488 nested additions plus a numeric leaf cost of 2 reaches 990. One more nested addition exceeds the budget. The existing product nesting error remains unchanged.

Graph traversal also has a dependency-depth limit of **991**. Demand expansion has its own depth limit of **991**. Each traversed dependency edge adds one. Nested expression edges also count during demand discovery. These limits remain separate from an optional limit on distinct measure/date pairs.

The accepted policy is recorded in [DEVIATIONS.md](../DEVIATIONS.md).

## Response serialization

The shared numeric response serializer keeps a fixed response depth boundary. The root has depth zero. Descendants through depth 255 are admitted. A deeper response uses a handled 500 error after the domain action. An accepted mutation therefore remains committed if its response exceeds that boundary. Numeric markers represent scalar values and do not add container depth. Request-validation errors retain their separate response path.

The shared numeric JSON helper also preserves exact integer tokens and normalized float tokens in stored formulas and manual fingerprints. Wire result types reuse the Goal contracts. Internal formula and request types add numeric metadata through `NumericJSON`.
