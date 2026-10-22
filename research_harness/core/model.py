"""Enumerations and constants shared by every module (single source of truth)."""
from __future__ import annotations

import re
from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # json / f-string friendly
        return self.value


class IntegrityStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class WorkflowStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    INCOMPLETE = "INCOMPLETE"
    COMPLETE = "COMPLETE"


class ReviewStatus(StrEnum):
    """Scientific review status. Never implies absolute scientific truth."""
    UNASSESSED = "UNASSESSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    AUTHOR_APPROVED = "AUTHOR_APPROVED"
    INDEPENDENTLY_REVIEWED = "INDEPENDENTLY_REVIEWED"
    REJECTED = "REJECTED"


class ReleaseEligibility(StrEnum):
    BLOCKED = "BLOCKED"
    ELIGIBLE = "ELIGIBLE"


class Decision(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CONFLICT = "CONFLICT"
    DUPLICATE = "DUPLICATE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    UNAUTHORIZED = "UNAUTHORIZED"


class Lifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"


class OriginKind(StrEnum):
    # Arrived through the proposal gateway. The *content author* (a chatbot, Codex or a human)
    # is unverified; only the importing actor is authenticated. Never equals "verified".
    PROPOSAL_IMPORTED = "PROPOSAL_IMPORTED"
    LEGACY_MIGRATED = "LEGACY_MIGRATED"  # adapted from v8.4.4 registries (never verified)
    SYSTEM_DERIVED = "SYSTEM_DERIVED"    # produced by the engine (e.g. Approval)


OBJECT_TYPES = (
    "ResearchQuestion", "Theory", "Construct", "Mechanism", "Proxy", "Dataset", "Source",
    "EvidencePassage", "Analysis", "Result", "Claim", "ManuscriptArtifact", "Adjudication",
    "Approval",
    # 9.2 knowledge types (appended: existing types and their order are unchanged)
    "DomainWorkflow", "MethodPlaybook", "JournalPolicy", "KnowledgeItem", "ContributionArgument", "AnalysisPlan",
    # M5 (appended): a researcher's goal with its plan and checkpoints; written by the task engine, never proposed
    "ResearchTask",
    # M5 WP4 (appended): a phenomenon or puzzle that is not yet a question, with several readings kept side by side
    "ResearchProblem",
    # M5 WP5 (appended): one execution of the internal retrieval over the corpus, with its queries, hits and coverage
    "RetrievalRun",
    # M5 WP6 (appended): the landscape of the literature on a problem: entries with their passages, evidence dimensions and suite inferences
    "Landscape",
)
SERVER_ONLY_TYPES = frozenset({"Approval", "Adjudication", "ResearchTask", "ResearchProblem", "RetrievalRun", "Landscape"})  # can never be created through a proposal

CLAIM_TIERS = (
    "descriptive", "associational", "predictive", "mechanism_consistent", "causal",
    "measurement_validation", "conceptual",
)
LEGACY_CLAIM_TIER = "unclassified"  # only the migration adapter may use it
FORMATION_BASES = ("exploratory", "confirmatory", "unknown")

ROLES = ("owner", "importer", "author", "independent_reviewer")

ID_PATTERN = r"^[A-Z][A-Z0-9_]{1,63}$"
ID_RE = re.compile(ID_PATTERN)
PROJECT_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$"
PROJECT_ID_RE = re.compile(PROJECT_ID_PATTERN)
ACTOR_ID_PATTERN = r"^[a-z][a-z0-9_\-\.]{1,63}$"
ACTOR_ID_RE = re.compile(ACTOR_ID_PATTERN)
SHA256_PATTERN = r"^[0-9a-f]{64}$"

# Fields no agent-supplied document may contain, at any depth. They are either server-assigned
# (identity, time, revision, digests) or authority-bearing (approval / verification).
FORBIDDEN_KEYS = frozenset({
    "approved_by", "approved_at", "verified_by", "verified_at", "release_eligible",
    "scientifically_verified", "verified", "approval", "approvals", "reviewer", "reviewed_by",
    "reviewed_at", "adjudicator_id", "claim_author_id", "independence_verdict",
    "scientific_review_status", "review_status", "integrity_status", "import_actor", "actor",
    "actor_id", "created_at", "updated_at", "created_by", "submitted_by", "timestamp",
    "revision", "content_digest", "revision_digest", "state_revision_committed",
    "is_independent", "independently_reviewed", "lifecycle_status", "role", "roles", "token",
})
SECRET_KEY_RE = re.compile(r"(password|passwd|secret|api[_-]?key|credential|private[_-]?key|access[_-]?token|bearer)", re.I)
