"""Behaviour that belongs to the in-memory conversation system alone.

The fake is not only a conformance subject: two supervisor seam tests reach the HTTP API
through it, and what they lean on it for is reply debt and idempotent replay. A message
sent twice under the same sender id is accepted once and reported fresh once, and a turn
that ends without the reply it was asked for leaves a marker saying so. If the fake drifted
on either, those tests would keep passing while describing something the real system does
not do — so the fidelity is pinned here, where the fake is the subject.
"""

from __future__ import annotations
