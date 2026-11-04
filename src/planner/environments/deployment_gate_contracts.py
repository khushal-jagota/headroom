"""Local proof exchanged by release checks and the deployment transaction."""

from typing import TypedDict


class HealthObservation(TypedDict):
    status: int | None
    body: str | None
    error: str | None


class ServedObservation(TypedDict):
    current_root: str
    health_url: str
    manifest_state: str
    manifest: str | None
    health: HealthObservation
    base_sha: str | None


class LegacyGateEvidence(TypedDict):
    requested_sha: str
    artifact_digest: str
    observation: ServedObservation


class PublicationGateEvidence(TypedDict):
    requested_sha: str
    artifact_digest: str
    publication_run_id: int
    archive_sha256: str


type GateEvidence = LegacyGateEvidence | PublicationGateEvidence
