"""Retain unresolved deployment evidence and ask Chief through the ordinary message door.

Receipts live beside the lifecycle, outside database snapshots and disposable execution
files. A pending receipt is never evidence that an agent received a prompt.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryQueued,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
)
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL
from planner.environments.deployment_attention_contracts import DeploymentAttention
from planner.environments.deployment_lifecycle import (
    DeploymentLifecycleStore,
    _atomic_write,
    _exclusive_lock,
)
from planner.message_delivery import service as message_delivery


class DeploymentAttentionStore:
    """Keep one immutable message and its receipt for each observed evidence identity."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, identity: str) -> Path:
        return self.directory / (hashlib.sha256(identity.encode()).hexdigest() + ".json")

    def read(self, identity: str) -> DeploymentAttention | None:
        path = self.path_for(identity)
        try:
            return DeploymentAttention(**json.loads(path.read_text()))
        except FileNotFoundError:
            return None

    def save(self, attention: DeploymentAttention) -> DeploymentAttention:
        path = self.path_for(attention.evidence_identity)
        with _exclusive_lock(path.with_suffix(".lock")):
            _atomic_write(path, asdict(attention))
        return attention

    def ensure(self, attention: DeploymentAttention) -> DeploymentAttention:
        path = self.path_for(attention.evidence_identity)
        with _exclusive_lock(path.with_suffix(".lock")):
            existing = self.read(attention.evidence_identity)
            if existing is not None:
                return existing
            _atomic_write(path, asdict(attention))
        return attention

    def supersede_others(self, identity: str) -> None:
        for path in self.directory.glob("*.json"):
            attention = DeploymentAttention(**json.loads(path.read_text()))
            if attention.evidence_identity != identity and attention.status not in {
                "resolved", "superseded"
            }:
                self.save(replace(attention, status="superseded"))


class DeploymentAttentionObserver:
    """Call poll periodically, including while ordinary candidate startup remains held."""

    def __init__(
        self,
        *,
        lifecycle_store: DeploymentLifecycleStore,
        attention_directory: Path,
        app_sha: str | None,
        clock: Clock,
        connect_database: Callable[[], sqlite3.Connection],
        conversation_system: Callable[[], ConversationSystem | None],
        startup_pending: Callable[[], bool],
        admission_closed: Callable[[], bool],
    ) -> None:
        self.lifecycle = lifecycle_store
        self.store = DeploymentAttentionStore(attention_directory)
        self.app_sha = app_sha
        self.clock = clock
        self.connect_database = connect_database
        self.conversation_system = conversation_system
        self.startup_pending = startup_pending
        self.admission_closed = admission_closed
        self._poll_lock = asyncio.Lock()

    def _observe(self) -> tuple[DeploymentAttention | None, bool]:
        read = self.lifecycle.read()
        record = read.lifecycle
        if record is None:
            if read.reason == "deployment status has not been recorded":
                return None, False
            # This explicitly names unavailable evidence, never a fabricated attempt.
            identity = f"unavailable:{self.lifecycle.path.resolve()}:{read.reason}"
            deployment_id = None
            evidence = f"The deployment record is unavailable: {read.reason}."
        else:
            identity = f"deployment:{record.deployment_id}:{record.requested_sha}"
            deployment_id = record.deployment_id
            self.store.supersede_others(identity)
            prior = self.store.read(identity)
            unknown = record.phase == "unknown" or (
                record.phase in {"preparing", "restarting", "verifying", "app_healthy"}
                and self.clock.now() >= record.expires_at
            )
            if not unknown:
                resolved = record.phase in {"failed", "rolled_back", "cancelled"} or (
                    record.phase == "succeeded" and record.serving_sha == self.app_sha
                )
                if prior is not None and resolved:
                    if prior.status != "resolved":
                        prior = self.store.save(replace(prior, status="resolved", detail=None))
                return prior, False
            evidence = (
                f"The requested revision is {record.requested_sha}. "
                f"The recorded phase is {record.phase}; its last update is "
                f"{record.updated_at.isoformat()}. "
                f"The recorded serving revision is {record.serving_sha or 'unconfirmed'}. "
                f"The server revision at observation is {self.app_sha or 'unconfirmed'}."
            )
        existing = self.store.read(identity)
        if existing is not None:
            return existing, True
        message = (
            "A deployment needs investigation because its final outcome is unconfirmed. "
            f"{evidence}\n"
            f"Read-only evidence: {self.lifecycle.path}. "
            f"Retained evidence for this exact observation: {self.store.path_for(identity)}. "
            "If another attempt owns the lifecycle, do not act on this notice. "
            "Inspect the finalizer and the workflow evidence. Establish the outcome and "
            "the smallest necessary action. Do not infer failure from a missing report. "
            "Do not restart, restore, run a deployment, or alter the lifecycle without "
            "owner authorization. Ask the owner only when a concrete decision or action "
            "needs him. This is a Panels system notice, with no reply requirement."
        )
        return self.store.ensure(DeploymentAttention(
            evidence_identity=identity,
            deployment_id=deployment_id,
            sender_message_id=(
                "deployment-attention-" + hashlib.sha256(identity.encode()).hexdigest()
            ),
            message=message,
        )), True

    async def poll(self) -> DeploymentAttention | None:
        async with self._poll_lock:
            attention, uncertain = self._observe()
            if attention is None or attention.status != "pending" or not uncertain:
                return attention
            system = self.conversation_system()
            if (
                self.startup_pending() or self.admission_closed() or system is None
                or system.workspace_admission.closed
            ):
                return attention
            conn = self.connect_database()
            try:
                result = await message_delivery.send_message(
                    system, conn, self.clock, RequestContext(OWNER_PRINCIPAL),
                    CHIEF_PRINCIPAL, attention.message,
                    sender_message_id=attention.sender_message_id,
                    expects_reply=False, system_message=True,
                )
            except Exception as exc:
                return self.store.save(replace(attention, detail=f"Chief delivery failed: {exc}"))
            finally:
                conn.close()
            if isinstance(result.fate, PromptDeliveryStarted):
                attention = replace(attention, status="started", detail=None)
            elif isinstance(result.fate, PromptDeliveryQueued):
                attention = replace(attention, status="queued", detail=None)
            elif isinstance(result.fate, PromptDeliveryUncertain):
                attention = replace(
                    attention, status="delivery_uncertain",
                    detail="Chief prompt delivery is uncertain. Automatic retry is held.",
                )
            else:
                attention = replace(attention, detail="Chief prompt was not admitted.")
            return self.store.save(replace(attention, conversation_id=result.conversation_id))
