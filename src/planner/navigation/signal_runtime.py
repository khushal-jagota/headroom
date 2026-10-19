"""Bind the admitted Signal producer to private navigation and delivery adapters.

The caller owns bootstrap, runtime ownership and outcome repair. Recovery here
only installs a new complete attachment after that caller establishes readiness.
"""

from __future__ import annotations

from planner.conversation.contracts import (
    SignalAttachment,
    SignalInvalidation,
    SignalPublicationSource,
)
from planner.navigation.delivery import DeliveryRuntime
from planner.navigation.signal_publication import delivery_authority, install, participate


class SignalNavigationBinding:
    def __init__(self, source: SignalPublicationSource, delivery: DeliveryRuntime) -> None:
        self.source = source
        self.delivery = delivery
        self._attachment: SignalAttachment | None = None
        self.delivery.bind_signal_source(source)
        self.delivery.admission.configure(None)

    def _unavailable(self, invalidation: SignalInvalidation) -> None:
        self.delivery.admission.invalidate()

    def attach(self) -> SignalAttachment:
        """Install complete producer state before admitting current delivery."""
        if self._attachment is not None:
            raise RuntimeError("Signal navigation is already attached")
        self.delivery.admission.configure(None)
        attachment = self.source.attach_signal_publication(
            install=install,
            participate=participate,
            unavailable=self._unavailable,
        )
        self._attachment = attachment
        attachment.validate(attachment.capture_permit())
        self.delivery.admission.configure(delivery_authority(attachment))
        return attachment

    def recover(self) -> SignalAttachment:
        """Reinstall only; the caller must first resolve and confirm domain repair."""
        self.close()
        return self.attach()

    def close(self) -> None:
        """Stop delivery and detach without releasing runtime ownership."""
        if self._attachment is not None:
            self._attachment.close()
            self._attachment = None
        self.delivery.admission.configure(None)
