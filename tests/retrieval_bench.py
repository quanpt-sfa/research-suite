"""A fixed synthetic corpus with planted difficulties, and its gold relevance labels (M5 WP5 retrieval benchmark).

Invented text, deterministic (no randomness). Families:
  A  uses the query's own words (financial reporting fraud, misstatement);
  B  the same idea under other names (earnings manipulation, AAER, accruals, restatement) and never the query words; one work exists in three versions
     (two working papers and the journal article), one more paper is held as an abstract only;
  C  the same idea described as accounting irregularities / falsified statements; one is held as metadata only;
  D  decoys that share the query's generic words: card-transaction fraud, intrusion detection, medical detection, bank runs, and financial reporting quality
     without fraud.
The gold labels say which full-text sources answer each query and which relevant papers are held but not readable.
"""
from __future__ import annotations

from typing import Any

from research_harness.knowledge import corpus

A = {
    "intro": ["Financial reporting fraud imposes large costs on investors, auditors and regulators.",
              "We study the detection of financial reporting fraud with firm-level accounting data.",
              "Detected misstatements in annual reports are rare, so models of fraud face severe class imbalance.",
              "Prior work on reporting fraud relies on accrual measures and financial ratios as predictors."],
    "method": ["We train gradient boosting and logistic models on annual filings to detect fraudulent financial reporting.",
               "The sample covers listed firms and labels each firm-year as fraud or non-fraud.",
               "Validation uses out-of-time splits so that detection is evaluated on later years only."],
    "results": ["The fraud detection model reaches an average precision well above a constant predictor.",
                "Misstatement detection accuracy differs across industries.",
                "These estimates describe prediction, not causes of reporting fraud."]}
B = {
    "intro": ["Earnings manipulation by managers distorts the information in accruals.",
              "Enforcement actions known as AAER mark firms whose earnings were manipulated.",
              "Restatement announcements reveal manipulation of earnings in earlier periods.",
              "Discretionary accruals are the standard proxy for the manipulation of earnings."],
    "method": ["We estimate discretionary accruals with a modified Jones model and relate them to AAER enforcement outcomes.",
               "The sample combines restatement records with annual financial data.",
               "Models are trained on earlier years and evaluated on later years."],
    "results": ["Firms targeted by AAER show larger abnormal accruals before the restatement.",
                "Earnings manipulation is more likely when managers hold options.",
                "The associations are not causal."]}
C = {
    "intro": ["Accounting irregularities lead to enforcement by the securities regulator.",
              "Falsified financial statements are uncovered by auditors, short sellers and whistleblowers.",
              "We examine how falsified statements are identified before enforcement."],
    "method": ["We collect cases of accounting irregularities from enforcement releases and classify how each was discovered.",
               "Each case is coded by two readers."],
    "results": ["Whistleblowers uncover most falsified statements in the sample.", "Auditors rarely identify the irregularities first."]}
D_CARD = {
    "intro": ["Credit card fraud detection must score each transaction within milliseconds.",
              "We study fraud detection on card transactions with streaming features.",
              "Fraud labels arrive weeks after the transaction through chargebacks."],
    "method": ["A recurrent network scores sequences of card transactions for fraud.", "Detection performance is measured by recall at a fixed false alarm rate."],
    "results": ["The model detects card fraud with high recall at low false alarm rates.", "Transaction features matter more than customer features."]}
D_NET = {
    "intro": ["Network intrusion detection looks for anomalies in packet flows.", "We study anomaly detection for encrypted traffic in enterprise networks."],
    "method": ["A autoencoder is trained on benign flows and flags large reconstruction errors.", "Detection thresholds are set on a validation window."],
    "results": ["The detector finds most attacks with few false alarms.", "Detection latency is under one second."]}
D_MED = {
    "intro": ["Tumor detection in radiology images supports early diagnosis.", "We study detection of lesions in computed tomography scans."],
    "method": ["A convolutional network is trained on annotated scans.", "Detection is evaluated with free-response curves."],
    "results": ["Lesion detection sensitivity exceeds that of a single reader.", "Small lesions remain hard to detect."]}
D_BANK = {
    "intro": ["Bank runs spread when depositors doubt the solvency of their bank.", "We study financial panics and the role of deposit insurance."],
    "method": ["A model of depositors choosing when to withdraw is solved numerically.", "Parameters are calibrated to historical panics."],
    "results": ["Deposit insurance reduces the probability of a run.", "Panics spread through common asset holdings."]}
D_QUAL = {
    "intro": ["Financial reporting quality affects the cost of capital.", "We study how audit fees relate to financial reporting quality in listed firms."],
    "method": ["We regress the cost of capital on a composite index of reporting quality.", "The sample covers listed firms over two decades."],
    "results": ["Higher reporting quality is associated with a lower cost of capital.", "Audit fees rise with firm complexity."]}

TITLES = {
    "A": ["Detecting Financial Reporting Fraud with Gradient Boosting", "Out of Time Validation for Fraud Detection Models", "Class Imbalance in Misstatement Prediction",
          "Ratios and Accruals as Predictors of Reporting Fraud", "Industry Differences in Fraud Detection Accuracy"],
    "B": ["Earnings Manipulation and Discretionary Accruals Before Enforcement", "Managerial Options and Earnings Manipulation", "Restatement Records and Abnormal Accruals",
          "Modified Jones Models and Enforcement Outcomes"],
    "C": ["How Falsified Financial Statements Come to Light", "Whistleblowers and Accounting Irregularities", "Auditors and the Discovery of Irregularities"],
}
REFS_DEFAULT = ["Standard setters publish accounting rules.", "A handbook of empirical methods."]


def _body(title: str, abstract: str, fam: dict, i: int, refs: list[str]) -> str:
    def pick(key: str, n: int) -> str:
        pool = fam[key]
        return " ".join(pool[(i + j) % len(pool)] for j in range(min(n, len(pool))))
    return (f"{title}\n\nAbstract\n{abstract}\n\n1 Introduction\n{pick('intro', 3)}\n\n2 Data and method\n{pick('method', 2)}\n\n3 Results\n{pick('results', 2)}\n\n"
            "References\n" + "\n".join(f"[{n}] {r}" for n, r in enumerate(refs, 1)) + "\n")


def build(store: Any, token: str) -> dict[str, Any]:
    """Import the corpus; return the ids and the gold labels."""
    ids: dict[str, list[str]] = {"A": [], "B": [], "C": [], "D": []}
    work: dict[str, str] = {}

    def put(key: str, fam_name: str, title: str, fam: dict, i: int, refs: list[str] | None = None, abstract: str | None = None, **meta: Any) -> str:
        text = _body(title, abstract or fam["intro"][i % len(fam["intro"])], fam, i, refs or REFS_DEFAULT)
        r = corpus.import_source(store, token, citation_key=key, text=text, title=title, year=2018 + (i % 6), **meta)
        ids[fam_name].append(r["object_id"])
        return r["object_id"]

    # family A: five papers; A02 cites B01 by its title
    for i, t in enumerate(TITLES["A"]):
        refs = list(REFS_DEFAULT)
        if i == 1:
            refs.append("Earnings Manipulation and Discretionary Accruals Before Enforcement. Journal of Synthetic Studies.")
        put(f"a{i + 1:02d}", "A", t, A, i, refs)
    # family B: B01, the three versions of B02, B03; B03 cites C01 by DOI and a medical paper by title
    put("b01", "B", TITLES["B"][0], B, 0)
    base_b02 = TITLES["B"][1]
    put("b02v1", "B", base_b02, B, 1, document_type="working_paper", review_status="NOT_REVIEWED", first_public_date="2024-02-01",
        date_evidence="repository posting record (synthetic)", version_id="v1", work_id="W_B02")
    put("b02v2", "B", base_b02, B, 1, document_type="working_paper", review_status="NOT_REVIEWED", first_public_date="2025-01-15",
        date_evidence="repository posting record (synthetic)", version_id="v2", work_id="W_B02")
    put("b02j", "B", base_b02, B, 1, document_type="journal_article", review_status="PUBLISHED", first_public_date="2026-06-30",
        date_evidence="DOI registration (synthetic)", version_id="journal", work_id="W_B02", version_relations={"is_version_of": "SRC_B02V2"})
    put("b03", "B", TITLES["B"][2], B, 2, [*REFS_DEFAULT, "doi:10.5555/synth.c01", "Tumor Detection in Radiology Images with Convolutional Networks. Synthetic Imaging."])
    # one relevant paper held as an abstract only, one as metadata only
    corpus.import_metadata_only(store, token, citation_key="b04", title=TITLES["B"][3], year=2022,
                                abstract="We estimate discretionary accruals with a modified Jones model and relate them to enforcement outcomes for manipulated earnings.")
    corpus.import_metadata_only(store, token, citation_key="c03", title=TITLES["C"][2], year=2021, venue="Journal of Synthetic Studies")
    ids["B"].append("SRC_B04")
    ids["C"].append("SRC_C03")
    put("c01", "C", TITLES["C"][0], C, 0, doi="10.5555/synth.c01")
    put("c02", "C", TITLES["C"][1], C, 1)
    # decoys
    for fam_name, fam, n, label in (("D", D_CARD, 6, "Card Transaction Fraud Detection"), ("D", D_NET, 6, "Network Intrusion Anomaly Detection"),
                                    ("D", D_MED, 6, "Medical Imaging Lesion Detection"), ("D", D_BANK, 4, "Bank Run Panic Models"),
                                    ("D", D_QUAL, 6, "Reporting Quality and the Cost of Capital")):
        for i in range(n):
            title = f"{label} Study {i + 1}" if label != "Medical Imaging Lesion Detection" or i else "Tumor Detection in Radiology Images with Convolutional Networks"
            key = f"d_{label.split()[0].lower()}_{i + 1}"
            put(key, fam_name, title, fam, i)
    full_text_relevant = [s for s in ids["A"] + ids["B"] + ids["C"] if s not in ("SRC_B04", "SRC_C03")]
    gold = {"k": 10, "top_n": 50, "queries": [
        {"id": "Q1", "text": "financial reporting fraud detection",
         "accepted_expansions": ["earnings manipulation", "accounting irregularities", "falsified financial statements", "AAER", "restatement", "misstatement"],
         "relevant": full_text_relevant, "relevant_not_full_text": ["SRC_B04", "SRC_C03"]},
        {"id": "Q2", "text": "managers manipulate earnings after enforcement", "accepted_expansions": ["discretionary accruals", "AAER", "restatement"],
         "relevant": [s for s in ids["B"] if s != "SRC_B04"], "relevant_not_full_text": ["SRC_B04"]}]}
    return {"ids": ids, "gold": gold, "work_versions": ["SRC_B02V1", "SRC_B02V2", "SRC_B02J"]}
