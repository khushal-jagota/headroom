# Plan review

The approved plan fits the existing contracts without adding a new data shape:

- the API already has the shared clock, configuration, and `resolve_day_id` seam used by Review;
- the board card already exposes the effective project identity required by the filter;
- the current client bucket builder accepts an arbitrary card slice;
- the URL-selected inspector can remain derived from the full day-scoped card set.

No unresolved contract violation was found. Implementation should keep project selection local to
the Workspace roster and prove the 5am boundary through the API seam rather than duplicating date
logic in the view or browser.
