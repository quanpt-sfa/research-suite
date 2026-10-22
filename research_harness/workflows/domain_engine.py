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
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Optional

from research_harness import taxonomy
from research_harness.state import workflow_runs
from research_harness.state.store import StateStore
from research_harness.workflows.status import Applicability, AssessmentStatus, ExecutionStatus

STAGES = ("contribution", "literature", "methods", "results", "discussion")
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
                           "instructions": s.instructions, "justification": s.justification} for n, s in self.stages.items()},
            "steps": [{"id": s.id, "stage": s.stage, "kind": s.kind, "title": s.title,
                       "entry_conditions": list(s.entry_conditions), "evidence_requirements": list(s.evidence_requirements),
                       "applicability": s.applicability, "on_condition": list(s.on_condition), "produces": list(s.produces)}
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
                "stage": {"applicable": st.applicable, "entry": list(st.entry_conditions), "instructions": st.instructions},
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
                stages[name] = StageSpec(name, applicable, tuple(raw.get("entry_conditions", [])), raw.get("instructions"),
                                         raw.get("justification"))
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
                steps.append(StepSpec(raw["id"], raw["stage"], raw.get("kind", "assess"), raw.get("title", ""),
                                      tuple(raw.get("entry_conditions", [])), tuple(raw.get("evidence_requirements", [])),
                                      raw.get("applicability"), tuple(raw.get("on_condition", [])), tuple(raw.get("produces", []))))
            ids = [s.id for s in steps]
            _require(len(set(ids)) == len(ids), "duplicate step ids")
            for s in steps:
                for rule in s.on_condition:
                    _require(rule["goto"] in ids, f"step {s.id}: goto '{rule['goto']}' is not a step of this workflow")
                for p in list(s.entry_conditions) + [r["when"] for r in s.on_condition]:
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

_PREDICATES = {"exists", "count_ge", "field_in", "study_design_in", "research_purpose_in", "step_execution", "step_assessment",
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

    def purposes(self) -> set:
        return set(self.research_purposes) | ({self.research_purpose} if self.research_purpose else set())

    def matching(self, otype: str, where: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        out = []
        for o in self.objects:
            if o["object_type"] != otype:
                continue
            content = o["content"]
            if all((content.get(k) == v) or (isinstance(content.get(k), list) and v in content.get(k)) for k, v in (where or {}).items()):
                out.append(o)
        return out


def facts_from_store(store: StateStore, *, research_question_id: Optional[str] = None, study_design: Optional[str] = None,
                     research_purpose: Optional[str] = None, problem_id: Optional[str] = None) -> ProjectFacts:
    """Snapshot the project: head objects, the research question's study design/scope, and domain step states."""
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
                if r["artifact_sha256"] and store.artifacts.has(r["artifact_sha256"]):
                    try:
                        diagnostics = json.loads(store.artifacts.get_bytes(r["artifact_sha256"])).get("result", {}).get("diagnostic_signals", {})
                    except ValueError:
                        diagnostics = {}
                steps[r["domain_step_id"]] = StepState(r["execution_status"], r["assessment_status"], diagnostics, r["run_id"], seq)
    inquiry: dict[str, Any] = {}
    if not problem_id and research_question_id:      # a question promoted from a ResearchProblem keeps the problem's recorded inquiry
        with store.read() as conn:
            row = conn.execute("SELECT e.target_id FROM edges e JOIN object_heads h ON h.object_id=e.source_id AND h.head_revision=e.source_revision "
                               "WHERE e.source_id=? AND e.relation='derived_from' AND e.provenance LIKE '%promoted_from%' ORDER BY e.rowid LIMIT 1", (research_question_id,)).fetchone()
        problem_id = row[0] if row else None
    if problem_id:                      # the kind of inquiry the researcher recorded on the ResearchProblem fills in what the question does not say
        prob = store.get_object(problem_id)
        if prob is None or prob["object_type"] != "ResearchProblem":
            raise KeyError(f"PROBLEM_NOT_FOUND: {problem_id}")
        inquiry = prob["content"].get("inquiry") or {}
    return ProjectFacts(objects=objects, study_design=study_design or rq_content.get("study_design") or inquiry.get("research_design"),
                        scope=dict(rq_content.get("scope") or {}), steps=steps, research_purpose=research_purpose or rq_content.get("research_purpose"),
                        research_purposes=tuple(inquiry.get("research_purposes") or ()))


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

    @property
    def blocked_by(self) -> list[str]:
        return self.unmet_entry + self.unmet_evidence

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step_id, "stage": self.stage, "kind": self.kind, "applicability": self.applicability.value,
                "applicability_reason": self.applicability_reason, "entry_met": self.entry_met, "evidence_met": self.evidence_met,
                "blocked_by": self.blocked_by, "done": self.done, "runnable": self.runnable, "reopened_by": self.reopened_by,
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


def stage_status(spec: DomainWorkflowSpec, facts: ProjectFacts, stage: str) -> StageEvaluation:
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
                                  unmet_entry + stage_ev.unmet_entry, not unmet_ev, unmet_ev, state, re, done, runnable))
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
    """Register that a step ran (by the researcher, the analysis plan or a workflow stage), with the diagnostic signals
    it produced. A repeat of a step links to the previous run of that step, so every return to a step stays in history."""
    spec.step(step_id)
    ExecutionStatus(execution), AssessmentStatus(assessment)
    for name, sig in (diagnostic_signals or {}).items():
        taxonomy.validate_term("diagnostic_signal", sig)
    body = json.dumps({"workflow": "domain_step", "result": {"step": step_id, "spec": spec.pin,
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
