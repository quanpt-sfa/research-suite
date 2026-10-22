"""Domain workflows as execution contracts (plan v3, Phase 3A.1; invariant I18).

A `DomainWorkflowSpec` is not a bundle of prompts. It declares *how work proceeds* in a field or study type:

* **stages** (contribution, literature, methods, results, discussion): whether each applies to the study design, with
  a justification when it does not, and the conditions that must hold before it may run;
* **steps** inside a stage, each with `entry_conditions` (what must already be true), `evidence_requirements` (what
  must be recorded before the step can be done), an `applicability` rule, and `on_condition` rules that send the work
  back to an earlier step when a recorded diagnostic says so.

Two workflows can therefore differ in step order, entry conditions, evidence requirements, applicable steps and
loop-back behaviour while sharing identical stage instructions. `evaluate`/`next_steps`/`explain` are deterministic
functions of the spec and the project's recorded facts.

Three layers are kept apart. The **scientific activity** is what researchers actually do (search, reconstruct, interpret, estimate); a **stage** here is a capability group of
the suite that supports an activity (it is not a manuscript section); a **manuscript section** is how a paper presents it, and a genre may not have a section of that name. A stage
is `NOT_APPLICABLE` only when the function it names is not part of the inquiry at all; a design without an estimation stage still has a method and findings, and its spec says what they
are (`StageSpec.activity`, `manuscript`) and which steps carry them. A step is done in one of three ways, and the engine says which: `EXECUTED_BY_CAPABILITY` (a server-written object that
only a capability produces exists), `DECLARED_IN_RECORD` (a structured record by the researcher exists and passed the suite's completeness checks) or `RECORDED_BY_RESEARCHER`
(`record_step_run`: an assertion, nothing was checked).

Conditions are written in a small closed language of *predicates* (no `eval`, no code):

    {"exists": {"type": T, "where": {field: value}}}          {"count_ge": {"type": T, "where": {...}, "n": k}}
    {"field_in": {"type": T, "field": f, "values": [...]}}     {"study_design_in": [...]}
    {"step_execution": {"step": id, "is": "SUCCEEDED"}}        {"step_assessment": {"step": id, "at_least": "PROVISIONAL"}}
    {"diagnostic_signal": {"step": id, "diagnostic": name, "is": "CONCERN_INDICATED"}}
    {"scope_matches": {dimension: value-or-list}}              {"all": [...]}  {"any": [...]}  {"not": pred}

`where` matches a field equal to the value, or a list field containing it. A predicate that is not in this language is
rejected when the spec is loaded, so a spec cannot hide logic.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from importlib import resources
from typing import Any, Optional

from research_harness import taxonomy
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows.status import Applicability, AssessmentStatus, ExecutionStatus

STAGES = ("contribution", "literature", "methods", "results", "discussion")
COMPLETION_KINDS = ("EXECUTED_BY_CAPABILITY", "DECLARED_IN_RECORD", "RECORDED_BY_RESEARCHER")
STAGE_ASSESSORS = ("skill", "steps")
STEP_KINDS = ("assess", "decide", "plan", "execute", "review")
_ASSESSMENT_RANK = {"NOT_ASSESSED": 0, "PROVISIONAL": 1, "VERIFIED_WITHIN_SCOPE": 2}


class SpecError(ValueError):
    """The domain workflow spec is malformed."""


# ---------------------------------------------------------------------------------------------------
# spec
# ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class StageSpec:
    name: str
    applicable: Any = True              # bool, or a predicate
    entry_conditions: tuple = ()
    instructions: Optional[str] = None  # stage instruction fragment appended to the model's system prompt
    justification: Optional[str] = None  # required reading when the stage does not apply
    activity: Optional[str] = None      # the scientific activity this stage supports in this kind of inquiry (not the manuscript section)
    manuscript: Optional[str] = None    # how a paper typically presents it; a genre may present it otherwise or merge it with another section
    assessed_by: str = "skill"          # "skill": the empirical stage executor; "steps": the stage run reports the state of this stage's steps under the contract


@dataclass(frozen=True)
class StepSpec:
    id: str
    stage: str
    kind: str
    title: str = ""
    entry_conditions: tuple = ()
    evidence_requirements: tuple = ()
    applicability: Optional[dict] = None   # {"when": predicate, "justification": str}
    on_condition: tuple = ()               # ({"when": predicate, "goto": step_id, "reason": str}, ...)
    produces: tuple = ()
    completed_by: Optional[dict] = None    # {"when": predicate, "kind": COMPLETION_KINDS member}: the step is done when recorded objects show the work exists


@dataclass(frozen=True)
class DomainWorkflowSpec:
    id: str
    version: str
    domain: str
    subfield: Optional[str]
    study_designs: tuple
    stages: dict
    steps: tuple
    scope: dict = field(default_factory=dict)
    exclusions: tuple = ()
    validation_status: str = "EXTRACTED"
    description: str = ""
    research_purposes: tuple = ()        # the purposes the workflow covers; empty = any (not part of the dict or digest when empty)

    def step(self, step_id: str) -> StepSpec:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(f"STEP_NOT_FOUND: {step_id}")

    def to_dict(self) -> dict[str, Any]:
        out = {
            "id": self.id, "version": self.version, "domain": self.domain, "subfield": self.subfield,
            "study_designs": list(self.study_designs), "description": self.description,
            "stages": {n: {"applicable": s.applicable, "entry_conditions": list(s.entry_conditions),
                           "instructions": s.instructions, "justification": s.justification,
                           **({"activity": s.activity} if s.activity else {}), **({"manuscript": s.manuscript} if s.manuscript else {}),
                           **({"assessed_by": s.assessed_by} if s.assessed_by != "skill" else {})} for n, s in self.stages.items()},
            "steps": [{"id": s.id, "stage": s.stage, "kind": s.kind, "title": s.title,
                       "entry_conditions": list(s.entry_conditions), "evidence_requirements": list(s.evidence_requirements),
                       "applicability": s.applicability, "on_condition": list(s.on_condition), "produces": list(s.produces),
                       **({"completed_by": s.completed_by} if s.completed_by else {})}
                      for s in self.steps],
            "scope": self.scope, "exclusions": list(self.exclusions), "validation_status": self.validation_status,
        }
        if self.research_purposes:       # a spec that does not use purposes keeps exactly the dict (and the digest, and every stored pin) it had
            out["research_purposes"] = list(self.research_purposes)
        return out

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()

    @property
    def pin(self) -> str:
        return f"{self.id}@{self.version}#{self.digest()[:16]}"

    def stage_instructions(self, stage: str) -> Optional[str]:
        st = self.stages.get(stage)
        return st.instructions if st else None

    def stage_digest(self, stage: str) -> str:
        """Digest of what governs one stage (its rules, its steps, the spec's identity): the part of the spec that a run
        of that stage depends on, so editing another stage does not invalidate it."""
        st = self.stages[stage]
        body = {"id": self.id, "version": self.version, "designs": list(self.study_designs),
                **({"purposes": list(self.research_purposes)} if self.research_purposes else {}),
                "stage": {"applicable": st.applicable, "entry": list(st.entry_conditions), "instructions": st.instructions,
                          **({"activity": st.activity} if st.activity else {}), **({"assessed_by": st.assessed_by} if st.assessed_by != "skill" else {})},
                "steps": [s for s in self.to_dict()["steps"] if s["stage"] == stage]}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DomainWorkflowSpec":
        try:
            taxonomy.validate_domain(data["domain"], data.get("subfield"))
            designs = tuple(taxonomy.validate_term("study_design", d) for d in data.get("study_designs", []))
            purposes = tuple(taxonomy.validate_term("research_purpose", d) for d in data.get("research_purposes", []))
            stages: dict[str, StageSpec] = {}
            for name in STAGES:
                raw = (data.get("stages") or {}).get(name, {})
                applicable = raw.get("applicable", True)
                _check_predicate(applicable) if isinstance(applicable, dict) else _require(isinstance(applicable, bool), f"stage {name}: applicable must be a bool or a predicate")
                if applicable is False:
                    _require(bool(raw.get("justification")), f"stage {name} is not applicable: a justification is required")
                for p in raw.get("entry_conditions", []):
                    _check_predicate(p)
                _require(raw.get("assessed_by", "skill") in STAGE_ASSESSORS, f"stage {name}: assessed_by must be one of {STAGE_ASSESSORS}")
                stages[name] = StageSpec(name, applicable, tuple(raw.get("entry_conditions", [])), raw.get("instructions"),
                                         raw.get("justification"), raw.get("activity"), raw.get("manuscript"), raw.get("assessed_by", "skill"))
            steps = []
            for raw in data.get("steps", []):
                _require(raw["stage"] in STAGES, f"step {raw['id']}: unknown stage '{raw['stage']}'")
                _require(raw.get("kind", "assess") in STEP_KINDS, f"step {raw['id']}: unknown kind '{raw.get('kind')}'")
                for p in raw.get("entry_conditions", []):
                    _check_predicate(p)
                for req in raw.get("evidence_requirements", []):
                    _require("type" in req and isinstance(req.get("min", 1), int), f"step {raw['id']}: malformed evidence requirement")
                if raw.get("applicability"):
                    _check_predicate(raw["applicability"]["when"])
                    _require(bool(raw["applicability"].get("justification")), f"step {raw['id']}: applicability needs a justification")
                for rule in raw.get("on_condition", []):
                    _check_predicate(rule["when"])
                    _require(bool(rule.get("reason")), f"step {raw['id']}: on_condition needs a reason")
                cb = raw.get("completed_by")
                if cb:
                    _check_predicate(cb["when"])
                    _require(cb.get("kind") in COMPLETION_KINDS[:2], f"step {raw['id']}: completed_by.kind must be one of {COMPLETION_KINDS[:2]}")
                steps.append(StepSpec(raw["id"], raw["stage"], raw.get("kind", "assess"), raw.get("title", ""),
                                      tuple(raw.get("entry_conditions", [])), tuple(raw.get("evidence_requirements", [])),
                                      raw.get("applicability"), tuple(raw.get("on_condition", [])), tuple(raw.get("produces", [])), cb))
            ids = [s.id for s in steps]
            _require(len(set(ids)) == len(ids), "duplicate step ids")
            for s in steps:
                for rule in s.on_condition:
                    _require(rule["goto"] in ids, f"step {s.id}: goto '{rule['goto']}' is not a step of this workflow")
                for p in list(s.entry_conditions) + [r["when"] for r in s.on_condition] + ([s.completed_by["when"]] if s.completed_by else []):
                    for ref in _referenced_steps(p):
                        _require(ref in ids, f"step {s.id}: predicate refers to unknown step '{ref}'")
            return cls(data["id"], data["version"], data["domain"], data.get("subfield"), designs, stages, tuple(steps),
                       dict(data.get("scope") or {}), tuple(data.get("exclusions") or ()),
                       data.get("validation_status", "EXTRACTED"), data.get("description", ""), purposes)
        except (KeyError, taxonomy.TaxonomyError) as exc:
            raise SpecError(f"invalid domain workflow spec: {type(exc).__name__}: {exc}") from exc


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise SpecError(message)


# ---------------------------------------------------------------------------------------------------
# predicates
# ---------------------------------------------------------------------------------------------------

REVIEW_COMPONENTS = ("search", "screening", "appraisal", "extraction", "synthesis")
_PREDICATES = {"exists", "count_ge", "field_in", "study_design_in", "research_purpose_in", "review_component", "step_execution", "step_assessment",
               "diagnostic_signal", "scope_matches", "all", "any", "not"}


def _check_predicate(p: Any) -> None:
    _require(isinstance(p, dict) and len(p) == 1, f"a predicate is a one-key object, got {p!r}")
    (name, arg), = p.items()
    _require(name in _PREDICATES, f"unknown predicate '{name}' (closed set: {sorted(_PREDICATES)})")
    if name in {"all", "any"}:
        _require(isinstance(arg, list) and arg, f"'{name}' needs a non-empty list")
        for sub in arg:
            _check_predicate(sub)
    elif name == "not":
        _check_predicate(arg)
    elif name == "study_design_in":
        _require(isinstance(arg, list), "'study_design_in' needs a list")
        for d in arg:
            taxonomy.validate_term("study_design", d)
    elif name == "research_purpose_in":
        _require(isinstance(arg, list), "'research_purpose_in' needs a list")
        for d in arg:
            taxonomy.validate_term("research_purpose", d)
    elif name == "review_component":
        _require(isinstance(arg, dict) and arg.get("component") in REVIEW_COMPONENTS and isinstance(arg.get("is"), list) and arg["is"],
                 f"'review_component' needs a component in {REVIEW_COMPONENTS} and a list 'is'")
    elif name in {"exists", "count_ge", "field_in"}:
        _require(isinstance(arg, dict) and "type" in arg, f"'{name}' needs a 'type'")
        if name == "count_ge":
            _require(isinstance(arg.get("n"), int), "'count_ge' needs an integer 'n'")
        if name == "field_in":
            _require("field" in arg and isinstance(arg.get("values"), list), "'field_in' needs 'field' and 'values'")
    elif name in {"step_execution", "step_assessment", "diagnostic_signal"}:
        _require(isinstance(arg, dict) and "step" in arg, f"'{name}' needs a 'step'")
        if name == "step_execution":
            ExecutionStatus(arg["is"])
        if name == "step_assessment":
            _require(arg.get("at_least") in _ASSESSMENT_RANK, "'step_assessment.at_least' must be NOT_ASSESSED, PROVISIONAL or VERIFIED_WITHIN_SCOPE")
        if name == "diagnostic_signal":
            taxonomy.validate_term("diagnostic_signal", arg["is"])
    elif name == "scope_matches":
        _require(isinstance(arg, dict), "'scope_matches' needs an object")
        for dim in arg:
            taxonomy.validate_term("scope_dimension", dim)


def _referenced_steps(p: dict) -> list[str]:
    (name, arg), = p.items()
    if name in {"all", "any"}:
        return [r for sub in arg for r in _referenced_steps(sub)]
    if name == "not":
        return _referenced_steps(arg)
    if name in {"step_execution", "step_assessment", "diagnostic_signal"}:
        return [arg["step"]]
    return []


# ---------------------------------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------------------------------

@dataclass
class StepState:
    execution: str
    assessment: str
    diagnostics: dict[str, str] = field(default_factory=dict)
    run_id: Optional[str] = None
    seq: int = 0  # position in the run registry: later runs have larger values
    source: str = "RECORDED_BY_RESEARCHER"   # how the step came to be done: see COMPLETION_KINDS (a stage run is EXECUTED_BY_CAPABILITY)


def _get_path(content: dict[str, Any], key: str) -> Any:
    """`a.b` reads content['a']['b']; a key without a dot is a plain field."""
    cur: Any = content
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _field_matches(got: Any, want: Any) -> bool:
    return got == want or (isinstance(got, list) and want in got)


@dataclass
class ProjectFacts:
    """What the engine knows about a project: its recorded objects, its study design and scope, and the state of
    each domain step (from the run registry)."""
    objects: list[dict[str, Any]] = field(default_factory=list)   # {"object_id","object_type","revision","content"}
    study_design: Optional[str] = None
    scope: dict[str, Any] = field(default_factory=dict)
    steps: dict[str, StepState] = field(default_factory=dict)
    research_purpose: Optional[str] = None
    research_purposes: tuple = ()      # all the recorded purposes (a ResearchProblem may record several); `research_purpose` is one of them or the question's own
    review: dict = field(default_factory=dict)            # {component: status} of the review protocol recorded on the ResearchProblem (`knowledge/review_protocol.py`)
    inquiry_source: Optional[dict] = None                 # {"problem_id", "revision", "pinned"}: which ResearchProblem revision the inquiry was read from

    def purposes(self) -> set:
        return set(self.research_purposes) | ({self.research_purpose} if self.research_purpose else set())

    def matching(self, otype: str, where: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        out = []
        for o in self.objects:
            if o["object_type"] != otype:
                continue
            content = o["content"]
            if all(_field_matches(_get_path(content, k), v) for k, v in (where or {}).items()):
                out.append(o)
        return out


def _pinned_problem(conn: Any, rq_id: str) -> Optional[tuple[str, int]]:
    """The ResearchProblem revision a question was promoted from, as pinned by the `derived_from` edge of the question's current revision."""
    row = conn.execute("SELECT e.target_id, e.target_revision FROM edges e JOIN object_heads h ON h.object_id=e.source_id AND h.head_revision=e.source_revision "
                       "WHERE e.source_id=? AND e.relation='derived_from' AND e.provenance LIKE '%promoted_from%' ORDER BY e.rowid LIMIT 1", (rq_id,)).fetchone()
    return (row[0], row[1]) if row else None


def facts_from_store(store: StateStore, *, research_question_id: Optional[str] = None, study_design: Optional[str] = None,
                     research_purpose: Optional[str] = None, problem_id: Optional[str] = None) -> ProjectFacts:
    """Snapshot the project: head objects, the research question's study design/scope, and domain step states.

    The kind of inquiry comes, in this order, from what is passed, from the question, and from a ResearchProblem. A question promoted from a problem reads the problem
    at the revision it was promoted from (the pin on its `derived_from` edge), never the problem's later head: reframing the problem does not change an old question's
    purpose or design until the researcher updates the question (`problem.update_question_inquiry`). A `problem_id` passed explicitly is read at its head."""
    objects, rq_content = [], {}
    with store.read() as conn:
        heads = conn.execute("SELECT object_id, object_type, head_revision FROM object_heads").fetchall()
        for oid, otype, rev in heads:
            obj = store.get_object(oid, revision=rev, conn=conn) or {}
            objects.append({"object_id": oid, "object_type": otype, "revision": rev, "content": obj.get("content") or {}})
            if otype == "ResearchQuestion" and (research_question_id in (None, oid)):
                rq_content = rq_content or (obj.get("content") or {})
        steps: dict[str, StepState] = {}
        if workflow_runs.has_registry(conn):
            for row in conn.execute("SELECT rowid AS _seq, * FROM workflow_runs WHERE domain_step_id IS NOT NULL AND legacy=0 ORDER BY rowid"):
                seq = row["_seq"]
                r = workflow_runs._decode({k: row[k] for k in row.keys() if k != "_seq"})
                diagnostics: dict[str, str] = {}
                source = "EXECUTED_BY_CAPABILITY" if r["workflow"] != "domain_step" else "RECORDED_BY_RESEARCHER"
                if r["artifact_sha256"] and store.artifacts.has(r["artifact_sha256"]):
                    try:
                        body = json.loads(store.artifacts.get_bytes(r["artifact_sha256"]))
                        diagnostics = body.get("result", {}).get("diagnostic_signals", {})
                        if r["workflow"] == "domain_step":
                            source = body.get("result", {}).get("source", source)
                    except ValueError:
                        diagnostics = {}
                steps[r["domain_step_id"]] = StepState(r["execution_status"], r["assessment_status"], diagnostics, r["run_id"], seq, source)
        pin: Optional[tuple[str, Optional[int]]] = None
        if problem_id:
            pin = (problem_id, None)
        elif research_question_id:
            pin = _pinned_problem(conn, research_question_id)
    inquiry: dict[str, Any] = {}
    review: dict[str, str] = {}
    source_info: Optional[dict[str, Any]] = None
    if pin:
        prob = store.get_object(pin[0], pin[1]) if pin[1] else store.get_object(pin[0])
        if prob is None or prob["object_type"] != "ResearchProblem":
            if problem_id:
                raise KeyError(f"PROBLEM_NOT_FOUND: {problem_id}")
        else:
            inquiry = prob["content"].get("inquiry") or {}
            source_info = {"problem_id": prob["object_id"], "revision": prob["revision"], "pinned": pin[1] is not None}
            # the review protocol is the working record of the method, not part of the inquiry: it is read at the problem's head, whatever revision the inquiry is pinned to
            head = store.get_object(prob["object_id"])
            if head and head["content"].get("review_protocol"):
                from research_harness.knowledge import review_protocol as RPROT
                review = {k: v["status"] for k, v in RPROT.component_statuses(head["content"]["review_protocol"]).items()}
    return ProjectFacts(objects=objects, study_design=study_design or rq_content.get("study_design") or inquiry.get("research_design"),
                        scope=dict(rq_content.get("scope") or {}), steps=steps, research_purpose=research_purpose or rq_content.get("research_purpose"),
                        research_purposes=tuple(inquiry.get("research_purposes") or ()), review=review, inquiry_source=source_info)


@dataclass
class Evaluation:
    ok: bool
    reason: str


def evaluate_predicate(p: dict, facts: ProjectFacts) -> Evaluation:
    (name, arg), = p.items()
    if name == "all":
        results = [evaluate_predicate(sub, facts) for sub in arg]
        bad = [r.reason for r in results if not r.ok]
        return Evaluation(not bad, "; ".join(bad))
    if name == "any":
        results = [evaluate_predicate(sub, facts) for sub in arg]
        return Evaluation(any(r.ok for r in results), "none of: " + " | ".join(r.reason for r in results))
    if name == "not":
        inner = evaluate_predicate(arg, facts)
        return Evaluation(not inner.ok, f"not ({inner.reason})" if inner.ok else "")
    if name == "study_design_in":
        return Evaluation(facts.study_design in arg, f"study design is {facts.study_design or 'not recorded'}, needs one of {arg}")
    if name == "research_purpose_in":
        return Evaluation(bool(facts.purposes() & set(arg)), f"research purpose is {sorted(facts.purposes()) or 'not recorded'}, needs one of {arg}")
    if name == "review_component":
        got = facts.review.get(arg["component"], "NOT_DECLARED")
        return Evaluation(got in arg["is"], f"the review protocol's {arg['component']} component is {got}, needs one of {arg['is']}")
    if name == "exists":
        n = len(facts.matching(arg["type"], arg.get("where")))
        return Evaluation(n >= 1, f"no {arg['type']} with {arg.get('where') or 'any content'} is recorded")
    if name == "count_ge":
        n = len(facts.matching(arg["type"], arg.get("where")))
        return Evaluation(n >= arg["n"], f"{n} {arg['type']} object(s) match {arg.get('where') or ''}, need {arg['n']}")
    if name == "field_in":
        ok = any(o["content"].get(arg["field"]) in arg["values"] for o in facts.matching(arg["type"]))
        return Evaluation(ok, f"no {arg['type']} has {arg['field']} in {arg['values']}")
    if name == "scope_matches":
        bad = [d for d, want in arg.items() if facts.scope.get(d) not in (want if isinstance(want, list) else [want])]
        return Evaluation(not bad, "project scope differs on " + ", ".join(f"{d} (is {facts.scope.get(d)!r})" for d in bad))
    state = facts.steps.get(arg["step"])
    if name == "step_execution":
        return Evaluation(bool(state and state.execution == arg["is"]), f"step '{arg['step']}' is {state.execution if state else 'not run'}, needs {arg['is']}")
    if name == "step_assessment":
        rank = _ASSESSMENT_RANK.get(state.assessment, -1) if state else -1
        return Evaluation(rank >= _ASSESSMENT_RANK[arg["at_least"]],
                          f"step '{arg['step']}' assessment is {state.assessment if state else 'not run'}, needs at least {arg['at_least']}")
    if name == "diagnostic_signal":
        got = (state.diagnostics.get(arg["diagnostic"]) if state else None)
        return Evaluation(got == arg["is"], f"diagnostic '{arg['diagnostic']}' of step '{arg['step']}' is {got or 'not recorded'}")
    raise SpecError(f"unknown predicate '{name}'")


# ---------------------------------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------------------------------

@dataclass
class StepEvaluation:
    step_id: str
    stage: str
    kind: str
    applicability: Applicability
    applicability_reason: Optional[str]
    entry_met: bool
    unmet_entry: list[str]
    evidence_met: bool
    unmet_evidence: list[str]
    state: Optional[StepState]
    reopened_by: Optional[dict[str, str]]
    done: bool
    runnable: bool
    completion: Optional[str] = None     # how a done step came to be done: one of COMPLETION_KINDS (None when it is not done)

    @property
    def blocked_by(self) -> list[str]:
        return self.unmet_entry + self.unmet_evidence

    @property
    def out_of_order(self) -> bool:
        """The work exists (a `completed_by` object) although the step's entry conditions are not met: for example a landscape built before the review protocol was adopted."""
        return self.done and self.completion not in (None, "RECORDED_BY_RESEARCHER") and not self.entry_met

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step_id, "stage": self.stage, "kind": self.kind, "applicability": self.applicability.value,
                "applicability_reason": self.applicability_reason, "entry_met": self.entry_met, "evidence_met": self.evidence_met,
                "blocked_by": self.blocked_by, "done": self.done, "runnable": self.runnable, "reopened_by": self.reopened_by, "completion": self.completion, "out_of_order": self.out_of_order,
                "state": None if not self.state else {"execution": self.state.execution, "assessment": self.state.assessment}}


@dataclass
class StageEvaluation:
    stage: str
    applicability: Applicability
    justification: Optional[str]
    entry_met: bool
    unmet_entry: list[str]

    @property
    def blocked(self) -> bool:
        return self.applicability is Applicability.APPLICABLE and not self.entry_met


def effective_facts(spec: DomainWorkflowSpec, facts: ProjectFacts) -> ProjectFacts:
    """The facts with every step whose `completed_by` predicate holds counted as done, labelled with how (`EXECUTED_BY_CAPABILITY`, `DECLARED_IN_RECORD`). A step the
    researcher only recorded (`record_step_run`) keeps the label `RECORDED_BY_RESEARCHER`; when both exist the step is labelled by the record that shows the work. Steps are
    resolved in order until nothing changes, so a step may be completed by objects that depend on an earlier derived step."""
    if not any(s.completed_by for s in spec.steps):
        return facts
    derived = dict(facts.steps)
    eff = replace(facts, steps=derived)
    for _ in range(len(spec.steps) + 1):
        changed = False
        for s in spec.steps:
            cb = s.completed_by
            if not cb or (derived.get(s.id) and derived[s.id].source == cb["kind"]):
                continue
            if evaluate_predicate(cb["when"], eff).ok:
                cur = derived.get(s.id)
                derived[s.id] = StepState("SUCCEEDED", "PROVISIONAL", dict(cur.diagnostics) if cur else {}, cur.run_id if cur else None, cur.seq if cur else 0, cb["kind"])
                changed = True
        if not changed:
            break
    return eff


def stage_status(spec: DomainWorkflowSpec, facts: ProjectFacts, stage: str) -> StageEvaluation:
    facts = effective_facts(spec, facts)
    st = spec.stages[stage]
    if spec.study_designs and facts.study_design is not None and facts.study_design not in spec.study_designs:
        return StageEvaluation(stage, Applicability.NOT_APPLICABLE,
                               f"{spec.id} covers study designs {list(spec.study_designs)}, not '{facts.study_design}'", True, [])
    if spec.research_purposes and facts.purposes() and not facts.purposes() & set(spec.research_purposes):
        return StageEvaluation(stage, Applicability.NOT_APPLICABLE,
                               f"{spec.id} covers research purposes {list(spec.research_purposes)}, not {sorted(facts.purposes())}", True, [])
    if st.applicable is False:
        return StageEvaluation(stage, Applicability.NOT_APPLICABLE, st.justification, True, [])
    if isinstance(st.applicable, dict):
        ev = evaluate_predicate(st.applicable, facts)
        if not ev.ok:
            return StageEvaluation(stage, Applicability.NOT_APPLICABLE, st.justification or ev.reason, True, [])
    unmet = [r.reason for r in (evaluate_predicate(p, facts) for p in st.entry_conditions) if not r.ok]
    return StageEvaluation(stage, Applicability.APPLICABLE, None, not unmet, unmet)


def evaluate(spec: DomainWorkflowSpec, facts: ProjectFacts) -> list[StepEvaluation]:
    facts = effective_facts(spec, facts)
    reopened: dict[str, dict[str, str]] = {}
    for s in spec.steps:
        for rule in s.on_condition:
            if not evaluate_predicate(rule["when"], facts).ok:
                continue
            # The rule fires only while the evidence for it is newer than the last run of the step it sends work to;
            # once that step has been redone, the loop is closed and the step is not reopened again.
            trigger = max((facts.steps[r].seq for r in _referenced_steps(rule["when"]) if r in facts.steps), default=0)
            target = facts.steps.get(rule["goto"])
            if target is None or trigger > target.seq:
                reopened.setdefault(rule["goto"], {"by": s.id, "reason": rule["reason"]})
    out = []
    for s in spec.steps:
        stage_ev = stage_status(spec, facts, s.stage)
        applicability, reason = stage_ev.applicability, stage_ev.justification
        if applicability is Applicability.APPLICABLE and s.applicability:
            ev = evaluate_predicate(s.applicability["when"], facts)
            if not ev.ok:
                applicability, reason = Applicability.NOT_APPLICABLE, s.applicability["justification"]
        if applicability is Applicability.NOT_APPLICABLE:
            out.append(StepEvaluation(s.id, s.stage, s.kind, applicability, reason, True, [], True, [], None, None, True, False))
            continue
        unmet_entry = [r.reason for r in (evaluate_predicate(p, facts) for p in s.entry_conditions) if not r.ok]
        unmet_ev = []
        for req in s.evidence_requirements:
            n = len(facts.matching(req["type"], req.get("where")))
            if n < req.get("min", 1):
                unmet_ev.append(f"needs {req.get('min', 1)} {req['type']} with {req.get('where') or 'any content'}"
                                + (f" ({req['about']})" if req.get("about") else "") + f"; {n} recorded")
        state = facts.steps.get(s.id)
        re = reopened.get(s.id)
        done = bool(state and state.execution == "SUCCEEDED") and re is None
        stage_blocked = not stage_ev.entry_met
        runnable = not done and not unmet_entry and not unmet_ev and not stage_blocked
        out.append(StepEvaluation(s.id, s.stage, s.kind, applicability, reason, not unmet_entry and not stage_blocked,
                                  unmet_entry + stage_ev.unmet_entry, not unmet_ev, unmet_ev, state, re, done, runnable, state.source if done and state else None))
    return out


def next_steps(spec: DomainWorkflowSpec, facts: ProjectFacts) -> list[str]:
    """Steps that can run now, in the spec's order; steps sent back by a loop-back rule come first."""
    ev = evaluate(spec, facts)
    runnable = [e for e in ev if e.runnable]
    return [e.step_id for e in sorted(runnable, key=lambda e: 0 if e.reopened_by else 1)]


def explain(spec: DomainWorkflowSpec, facts: ProjectFacts, step_id: str) -> dict[str, Any]:
    spec.step(step_id)  # KeyError if unknown
    return next(e for e in evaluate(spec, facts) if e.step_id == step_id).to_dict()


# ---------------------------------------------------------------------------------------------------
# recording step runs
# ---------------------------------------------------------------------------------------------------

def record_step_run(store: StateStore, spec: DomainWorkflowSpec, step_id: str, *, execution: str = "SUCCEEDED",
                    assessment: str = "PROVISIONAL", diagnostic_signals: Optional[dict[str, str]] = None,
                    research_question_id: Optional[str] = None, notes: str = "") -> str:
    """Register that a step ran, as an **assertion**: the run is labelled `RECORDED_BY_RESEARCHER` because nothing about the work was checked by the suite. A step whose work
    leaves recorded objects (`completed_by`) is labelled by those objects instead. Diagnostic signals the step produced can be given. A repeat of a step links to the previous run of that step, so every return to a step stays in history."""
    spec.step(step_id)
    ExecutionStatus(execution), AssessmentStatus(assessment)
    for name, sig in (diagnostic_signals or {}).items():
        taxonomy.validate_term("diagnostic_signal", sig)
    body = json.dumps({"workflow": "domain_step", "result": {"step": step_id, "spec": spec.pin, "source": "RECORDED_BY_RESEARCHER",
                                                              "diagnostic_signals": diagnostic_signals or {}, "notes": notes}},
                      sort_keys=True).encode("utf-8")
    sha = store.artifacts.put(body)
    with store.read() as conn:
        prev = conn.execute("SELECT run_id FROM workflow_runs WHERE domain_step_id=? AND legacy=0 ORDER BY rowid DESC LIMIT 1",
                            (step_id,)).fetchone()
    return workflow_runs.append_run(store, {
        "workflow": "domain_step", "research_question_id": research_question_id, "input_fingerprint": hashlib.sha256(body).hexdigest(),
        "executor": "domain_engine@1", "artifact_sha256": sha, "execution_status": execution, "context_status": "COMPLETE",
        "assessment_status": assessment, "applicability": "APPLICABLE", "legacy_status": "SUCCESS" if execution == "SUCCEEDED" else "FAILED",
        "domain_step_id": step_id, "domain_workflow_pin": spec.pin, "parent_run_id": prev[0] if prev else None})


# ---------------------------------------------------------------------------------------------------
# built-in specs
# ---------------------------------------------------------------------------------------------------

def _spec_files() -> dict[str, Any]:
    root = resources.files("research_harness.workflows").joinpath("domain_specs")
    return {p.name[:-5]: p for p in root.iterdir() if p.name.endswith(".json")}


def list_builtin_specs() -> list[str]:
    return sorted(json.loads(p.read_text(encoding="utf-8"))["id"] for p in _spec_files().values())


def load_builtin_spec(spec_id: str) -> DomainWorkflowSpec:
    for path in _spec_files().values():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["id"] == spec_id:
            return DomainWorkflowSpec.from_dict(data)
    raise KeyError(f"DOMAIN_WORKFLOW_NOT_FOUND: {spec_id}. Built in: {list_builtin_specs()}")


GENERIC_SPEC_ID = "LIB_DW_GENERIC"


def suggest_contracts(facts: ProjectFacts) -> dict[str, Any]:
    """Which built-in contracts fit the recorded inquiry. Nothing is chosen for the researcher: a fit needs the recorded design (if the spec lists designs) and a recorded
    purpose (if the spec lists purposes) to match; with no inquiry recorded, or no fit, the answer is `RESEARCHER_DECISION_REQUIRED` and every contract is listed with why it
    does or does not fit. The generic contract is never offered as 'the appropriate one': it fits anything because it says nothing."""
    purposes, design = facts.purposes(), facts.study_design
    rows = []
    for sid in list_builtin_specs():
        sp = load_builtin_spec(sid)
        why: list[str] = []
        if sp.id == GENERIC_SPEC_ID:
            rows.append({"spec": sid, "fit": "NO_GUIDANCE", "reasons": ["fits any inquiry because it adds no guidance: it is not a contract for a kind of inquiry"]})
            continue
        if sp.study_designs:
            why.append(f"design {design!r} " + ("is covered" if design in sp.study_designs else f"is not among {list(sp.study_designs)}") if design else "no design is recorded")
        if sp.research_purposes:
            why.append(f"purposes {sorted(purposes)} " + ("overlap" if purposes & set(sp.research_purposes) else f"do not overlap {list(sp.research_purposes)}") if purposes else "no purpose is recorded")
        design_mismatch = bool(sp.study_designs and design and design not in sp.study_designs)
        purpose_mismatch = bool(sp.research_purposes and purposes and not purposes & set(sp.research_purposes))
        unknown = bool((sp.study_designs and not design) or (sp.research_purposes and not purposes))
        fit = "DOES_NOT_FIT" if design_mismatch or purpose_mismatch else ("INQUIRY_INCOMPLETE" if unknown else "FITS_RECORDED_INQUIRY")
        rows.append({"spec": sid, "fit": fit, "reasons": why})
    fits = [r["spec"] for r in rows if r["fit"] == "FITS_RECORDED_INQUIRY"]
    status = "CANDIDATES_FOUND" if fits else ("INQUIRY_NOT_RECORDED" if not design and not purposes else "RESEARCHER_DECISION_REQUIRED")
    return {"status": status, "candidates": fits, "contracts": rows, "recorded": {"study_design": design, "research_purposes": sorted(purposes)},
            "note": "the researcher chooses the contract; a candidate is a contract whose declared designs and purposes match what the problem records" if fits else
                    "no contract is chosen: record the inquiry (design and purpose) on the problem, or choose a contract and say why"}


def resolve_spec(domain_workflow: Any = None, *, research_question_content: Optional[dict] = None) -> DomainWorkflowSpec:
    """A spec object, a spec dict, a built-in id, or (when none is given) the research question's `domain_workflow`
    field, falling back to the generic spec."""
    if isinstance(domain_workflow, DomainWorkflowSpec):
        return domain_workflow
    if isinstance(domain_workflow, dict):
        return DomainWorkflowSpec.from_dict(domain_workflow)
    if isinstance(domain_workflow, str):
        return load_builtin_spec(domain_workflow)
    declared = (research_question_content or {}).get("domain_workflow")
    return load_builtin_spec(declared) if isinstance(declared, str) else load_builtin_spec(GENERIC_SPEC_ID)
