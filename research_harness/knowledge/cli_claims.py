"""`research claims ...` and `research export claims` (M5 WP6.2)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Optional


def _short(text: Optional[str], n: int = 110) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _show_text(v: dict) -> None:
    cov = v["coverage"]
    cur = v["currency"]
    print(f"{v['inventory_id']} r{v['revision']} \"{v['label']}\": {cov['claims']} claims from {cov['sources']} source(s); {cov['model_calls']} model call(s); sources {cur['sources']['status']}")
    if not cur["all_current"]:
        print("  ! a Source was revised after this inventory was built: its claims, and every relation resting on them, need a second look")
    print("  by attribution: " + ", ".join(f"{k} {n}" for k, n in cov["claims_by_attribution"].items()) + "  |  by role: " + ", ".join(f"{k} {n}" for k, n in cov["claims_by_role"].items()))
    for c in v["claims"]:
        att = c["attribution"].get("value") or "NOT_ASSIGNED"
        flag = (c["attribution"].get("cue_check") or {}).get("flag")
        extras = []
        if c["needs_followup"]:
            extras.append("follow-up: " + ",".join(c["needs_followup"]))
        if c["currency"]["status"] not in ("CURRENT", "NOT_APPLICABLE"):
            extras.append(c["currency"]["status"])
        if c["status"] != "UNREVIEWED":
            extras.append(c["status"])
        src = f"{c['source_id']}@{c['source_revision']}" if c.get("source_id") else "no source (an interpretation)"
        print(f"  {c['id']} [{c['role']}] {att} ({c['attribution_status']}{', cue flag ' + flag if flag else ''}) {src}: {_short(c['text'])}")
        if c.get("wording"):
            print(f"      wording: \"{_short(c['wording']['quote'], 140)}\" · {len(c['evidence'])} link(s)" + (f" · {'; '.join(extras)}" if extras else ""))
    if v["relations"]:
        print("  relations (proposals; none is established until you decide):")
        for r in v["relations"]:
            to = r["to"].get("claim") or "passage " + (r["to"].get("passage") or {}).get("passage_id", "?")
            print(f"    {r['id']} {r['from']['claim']} -{r['kind']}-> {to} [{r['derivation']}] {r['confirmation']['state']}" + (f"; NEEDS REASSESSMENT: {'; '.join(r['needs_reassessment'])}" if r["needs_reassessment"] else ""))
    if v["alignments"]:
        print("  alignments across versions (proposals):")
        for a in v["alignments"]:
            e, l = a["earlier"], a["later"]
            print(f"    {a['id']} {a['change']} work {a['work_id']}: {(e or {}).get('claim', '-')} -> {(l or {}).get('claim', '-')} [{a['derivation']}] {a['confirmation']['state']}"
                  + (f"; NEEDS REASSESSMENT: {'; '.join(a['needs_reassessment'])}" if a["needs_reassessment"] else ""))
    print("  " + v["limits"][3])


def _corrections(items: Optional[list[str]]) -> dict[str, str]:
    out = {}
    for it in items or []:
        k, _, val = it.partition("=")
        out[k.strip()] = val.strip()
    return out


def register(sub: Any) -> None:
    from research_harness import cli_knowledge as K
    from research_harness.knowledge import claim_relations as CR
    from research_harness.knowledge import claims as CL

    def tok(a: argparse.Namespace) -> Optional[str]:
        return K._helpers()._resolve_token(a)

    p = sub.add_parser("claims", help="The claims of a set of papers: who holds each, what it rests on, relations and version changes (all proposals until you decide)")
    s = p.add_subparsers(dest="claims_cmd", required=True)

    def add(name: str, help_: str, fn: Callable, text: Optional[Callable] = None, *, token: bool = True, model: bool = False) -> argparse.ArgumentParser:
        q = s.add_parser(name, help=help_)
        K._common(q, token=token)
        if model:
            q.add_argument("--model")
        q.set_defaults(func=lambda a, fn=fn, text=text: K._run(a, lambda: fn(K._store(a), a), text))
        return q

    q = add("build", "Read the full text of these papers with the claims protocol (model calls, every quotation verified) and build an inventory",
            lambda st, a: CL.build(st, tok(a), a.sources, K._adapter(a), label=a.label, strategy=a.strategy, max_input_tokens=a.max_input_tokens),
            lambda o: print(f"{o['inventory_id']} r{o['revision']}: {o['claims']} claims from {o['sources']} source(s); {o['calls']} model call(s), {o['input_tokens']} input / {o['output_tokens']} output tokens "
                            "(as the adapter reports them)"), model=True)
    q.add_argument("sources", nargs="+")
    q.add_argument("--label")
    q.add_argument("--strategy", choices=list(CL.STRATEGIES), default="single_pass", help="single_pass: one reading of all sections (default); section_aware: one reading per section group")
    q.add_argument("--max-input-tokens", type=int, default=12000, dest="max_input_tokens")
    add("add-sources", "Read more papers into an inventory; the claims already there are kept",
        lambda st, a: CL.add_sources(st, tok(a), a.inventory, a.sources, K._adapter(a), strategy=a.strategy), lambda o: print(f"{o['inventory_id']} r{o['revision']}: {o.get('claims_added', 0)} claim(s) added"),
        model=True).add_argument("inventory")
    s.choices["add-sources"].add_argument("sources", nargs="+")
    s.choices["add-sources"].add_argument("--strategy", choices=list(CL.STRATEGIES), default="single_pass")

    add("show", "Claims with attribution, role and wording; relations and alignments with what you decided",
        lambda st, a: CL.view(st, a.inventory), _show_text, token=False).add_argument("inventory")
    add("list", "Claim inventories in this project", lambda st, a: {"inventories": CL.list_inventories(st)},
        lambda o: print("\n".join(f"{x['inventory_id']} r{x['revision']} {x['label']}: {x['claims']} claims, {x['relations']} relation(s), {x['alignments']} alignment(s)" for x in o["inventories"]) or "no inventories"),
        token=False)
    q = add("audit", "Claims whose attribution is not assigned or disagrees with the citation cues in their wording (a flag, never a decision)",
            lambda st, a: {"flags": CL.attribution_audit(CL.view(st, a.inventory))},
            lambda o: print("\n".join(f"{f['claim']} {f['source_id']}@{f['source_revision']} {f['flag']} (labelled {f['attribution']}): {_short(f['wording'])}" for f in o["flags"]) or "no flags"), token=False)
    q.add_argument("inventory")

    q = add("followup", "A closer reading of the claims that need one (a model asked for it, or the claim is compound); one call each, over a few sentences chosen by words alone",
            lambda st, a: CL.followup(st, tok(a), a.inventory, K._adapter(a), claim_ids=a.claim or None, reasons=tuple(a.reason) if a.reason else CL.FOLLOWUP_REASONS, max_claims=a.max_claims),
            lambda o: print(f"{o['claims_followed_up']} claim(s) followed up, {o['model_calls']} model call(s), {o['links_added']} link(s) added"
                            + (f"; left for the budget: {', '.join(o['claims_left_for_budget'])}" if o["claims_left_for_budget"] else "")), model=True)
    q.add_argument("inventory")
    q.add_argument("--claim", action="append")
    q.add_argument("--reason", action="append", choices=["NEEDS_DEEPER_READING", "COMPOUND_CLAIM", "NO_EVIDENCE_BEYOND_WORDING"])
    q.add_argument("--max-claims", type=int, dest="max_claims")

    q = add("review", "Your decision on a claim: CONFIRMED, CORRECTED (with --set attribution=...|role=...|text=...|granularity=...) or REJECTED",
            lambda st, a: CL.review_claim(st, tok(a), a.inventory, a.claim, a.decision, a.reason, corrections=_corrections(a.set) or None),
            lambda o: print(f"{o['inventory_id']} r{o['revision']}: {o['claim']} {o['decision']}"))
    q.add_argument("inventory")
    q.add_argument("claim")
    q.add_argument("decision", choices=["CONFIRMED", "CORRECTED", "REJECTED"])
    q.add_argument("--reason", required=True)
    q.add_argument("--set", action="append", metavar="FIELD=VALUE")

    q = add("link-cited", "Say which held Source a claim's citation refers to (it is still the citing authors' report, and nothing is checked)",
            lambda st, a: CL.link_cited_source(st, tok(a), a.inventory, a.claim, a.citation, a.source), lambda o: print(f"{o['inventory_id']} r{o['revision']}: {o['claim']} citation linked"))
    q.add_argument("inventory")
    q.add_argument("claim")
    q.add_argument("--citation", required=True)
    q.add_argument("--source", required=True)

    q = add("interpret", "Record your interpretation of what claims imply (no wording, never author-stated)",
            lambda st, a: CL.add_interpretation(st, tok(a), a.inventory, text=a.text, attribution="RESEARCHER_INTERPRETATION", about=a.about, rationale=a.rationale, role=a.role),
            lambda o: print(f"{o['inventory_id']} r{o['revision']}: {o['claim']} recorded"))
    q.add_argument("inventory")
    q.add_argument("--text", required=True)
    q.add_argument("--about", action="append", required=True)
    q.add_argument("--rationale", required=True)
    q.add_argument("--role", default="ASSUMPTION")

    q = add("add-evidence", "Add a passage to a claim (the quote must be in the paper's stored text)",
            lambda st, a: CL.add_evidence_link(st, tok(a), a.inventory, a.claim, quote=a.quote, role=a.role, why=a.why),
            lambda o: print(f"{o['inventory_id']} r{o['revision']}: {o['claim']} gained passage {o['passage']}"))
    q.add_argument("inventory")
    q.add_argument("claim")
    q.add_argument("--quote", required=True)
    q.add_argument("--role", required=True)
    q.add_argument("--why", required=True)

    q = add("candidates", "Claims of other works that share the words of this one (by words alone; a claim to read, not a relation)",
            lambda st, a: CL.candidates(st, a.inventory, a.claim, limit=a.limit),
            lambda o: print(o.get("note", "") + "\n" + "\n".join(f"  {c['claim']} {c['source_id']}@{c['source_revision']} score {c['score']}: {_short(c['text'])}" for c in o["candidates"])), token=False)
    q.add_argument("inventory")
    q.add_argument("claim")
    q.add_argument("--limit", type=int, default=10)

    q = add("relation-add", "Record your own relation between claims through the gate (JSON): {kind, from, to, rationale, ...}",
            lambda st, a: CR.add_relation(st, tok(a), a.inventory, K._json_arg(a.proposal, "--proposal"), provenance=a.provenance, critic=a.critic),
            lambda o: print(f"{o['inventory_id']} r{o['revision']}: {o['relation']} recorded (PROPOSED, unconfirmed)"))
    q.add_argument("inventory")
    q.add_argument("--proposal", required=True)
    q.add_argument("--provenance", choices=["researcher_proposed", "critic_proposed"], default="researcher_proposed")
    q.add_argument("--critic")
    q = add("relation-propose", "A model proposes relations between the claims (one call); each is checked by the gate and stays unconfirmed until you decide",
            lambda st, a: CR.propose_relations(st, tok(a), a.inventory, K._adapter(a), kinds=a.kind or None),
            lambda o: print(f"{o['status']}: {len(o['accepted'])} recorded ({', '.join(o['accepted']) or 'none'}), {len(o['rejected'])} rejected"
                            + "".join(f"\n  rejected {r['proposal'].get('kind')}: {r['code']}: {r['reason']}" for r in o["rejected"])) if o["status"] == "PROPOSED" else print(f"FAILED ({o['reason']}): {o['message']}"),
            model=True)
    q.add_argument("inventory")
    q.add_argument("--kind", action="append")
    q = add("relation-review", "Your decision on a relation: CONFIRMED, DISMISSED or NEEDS_SCOPE",
            lambda st, a: CR.review_relation(st, tok(a), a.inventory, a.relation, a.decision, a.reason, acknowledge_missing=a.acknowledge_missing),
            lambda o: print(f"{o['inventory_id']} r{o['revision']}: {o['id']} {o['decision']}"))
    q.add_argument("inventory")
    q.add_argument("relation")
    q.add_argument("decision", choices=["CONFIRMED", "DISMISSED", "NEEDS_SCOPE"])
    q.add_argument("--reason", required=True)
    q.add_argument("--acknowledge-missing", action="store_true", dest="acknowledge_missing")

    q = add("align-add", "Record your own alignment of a claim across two versions of one work (JSON): {change, earlier, later, what_changed, rationale, ...}",
            lambda st, a: CR.add_alignment(st, tok(a), a.inventory, K._json_arg(a.proposal, "--proposal")), lambda o: print(f"{o['inventory_id']} r{o['revision']}: {o['alignment']} recorded (PROPOSED)"))
    q.add_argument("inventory")
    q.add_argument("--proposal", required=True)
    q = add("align-propose", "A model proposes alignments between the versions of a work (one call); each is checked by the gate and stays unconfirmed",
            lambda st, a: CR.propose_alignments(st, tok(a), a.inventory, K._adapter(a), work_id=a.work),
            lambda o: print(f"{o['status']}: {len(o['accepted'])} recorded, {len(o['rejected'])} rejected" + "".join(f"\n  rejected {r['proposal'].get('change')}: {r['code']}: {r['reason']}" for r in o["rejected"]))
            if o["status"] == "PROPOSED" else print(f"FAILED ({o['reason']}): {o['message']}"), model=True)
    q.add_argument("inventory")
    q.add_argument("--work")
    q = add("align-review", "Your decision on an alignment: CONFIRMED, DISMISSED or NEEDS_SCOPE",
            lambda st, a: CR.review_alignment(st, tok(a), a.inventory, a.alignment, a.decision, a.reason, acknowledge_missing=a.acknowledge_missing),
            lambda o: print(f"{o['inventory_id']} r{o['revision']}: {o['id']} {o['decision']}"))
    q.add_argument("inventory")
    q.add_argument("alignment")
    q.add_argument("decision", choices=["CONFIRMED", "DISMISSED", "NEEDS_SCOPE"])
    q.add_argument("--reason", required=True)
    q.add_argument("--acknowledge-missing", action="store_true", dest="acknowledge_missing")
    q = add("align-candidates", "For the claims of one version, the claims of the next that share their words (candidates only; nothing is written)",
            lambda st, a: CR.alignment_candidates(st, a.inventory, a.work), lambda o: print(json.dumps(o, indent=1, default=str)), token=False)
    q.add_argument("inventory")
    q.add_argument("work")

    q = add("annotation-form", "A blank annotation form for one paper, to be filled by a person before any system output is seen; the suite never fills it",
            lambda st, a: _form(st, a), lambda o: print(f"blank form written to {o['written']} ({o['sections']} sections)"), token=False)
    q.add_argument("source")
    q.add_argument("--annotator")
    q.add_argument("--out", required=True)

    q = add("benchmark", "Score an inventory against independent gold judgements (JSON files, one per source) and record the result in the run registry",
            lambda st, a: _benchmark(st, tok(a), a), lambda o: print(json.dumps(o, indent=1, default=str)))
    q.add_argument("inventory")
    q.add_argument("--gold", action="append", required=True, help="Gold file for one source (repeatable); its source.source_id must be a Source of the inventory")
    q.add_argument("--cross", help="Gold file with relations and alignments written in gold claim ids")
    q.add_argument("--mode", choices=["exact", "overlap", "lexical"], default="overlap")
    q.add_argument("--threshold", type=float, default=0.5)
    q.add_argument("--record", action="store_true", help="Record the result in the run registry so the claims export includes it")

    ex = sub.choices["export"]._subparsers._group_actions[0]
    q = ex.add_parser("claims", help="Claims, evidence links, relations, version history, coverage, attribution audit, graph and token cost of an inventory, with a manifest")
    q.add_argument("inventory")
    q.add_argument("--out")
    q.add_argument("--into-repo", action="store_true", dest="into_repo")
    K._common(q, token=False)

    def go(a: argparse.Namespace) -> Any:
        from research_harness.export.claims import build_claims_export
        from research_harness.export.manifest import write_export
        st = K._store(a)
        return write_export(st, build_claims_export(st, a.inventory), Path(a.out) if a.out else None, into_repo=a.into_repo)

    q.set_defaults(func=lambda a: K._run(a, lambda: go(a), lambda o: print(f"exported to {o['out']} (closure {o['closure_digest'][:12]}): {', '.join(o['files'])}"
                                                                            + "".join(f"\n  NOT_AVAILABLE {n['file']}: {n['reason']}" for n in o["not_available"]))))


def _form(st: Any, a: argparse.Namespace) -> dict[str, Any]:
    from research_harness.benchmarks import claims as BM
    from research_harness.knowledge import corpus
    obj, text = corpus.source_text(st, a.source)
    form = BM.blank_form(a.source, obj["content"]["text_sha256"], corpus.source_sections(st, obj, text), annotator=a.annotator)
    Path(a.out).write_text(json.dumps(form, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"written": a.out, "sections": len(form["sections"])}


def _benchmark(st: Any, token: Optional[str], a: argparse.Namespace) -> dict[str, Any]:
    from research_harness.benchmarks import claims as BM
    from research_harness.knowledge import claims as CL
    view = CL.view(st, a.inventory)
    golds = {}
    for path in a.gold:
        g = BM.load_gold(path)
        sid = (g.get("source") or {}).get("source_id")
        if not sid or sid not in {s["source_id"] for s in view["sources"]}:
            raise BM.KnowledgeError("GOLD_SOURCE_UNKNOWN", f"{path}: source.source_id {sid!r} is not a Source of {a.inventory}")
        golds[sid] = g
    cross = json.loads(Path(a.cross).read_text(encoding="utf-8")) if a.cross else None
    res = BM.evaluate(view, golds, cross, mode=a.mode, threshold=a.threshold)
    if a.record:
        res["recorded_as_run"] = BM.record_results(st, res, scope=a.inventory, note="claims benchmark")
    return res
