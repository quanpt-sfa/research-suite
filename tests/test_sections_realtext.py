"""Section detection on text extracted from a real PDF (tuned on real noise, not on tidy fixtures)."""
from __future__ import annotations

from pathlib import Path

import pytest

from research_harness.workflows.sections import detect_sections, sections_for_dimension

EXCERPT = Path(__file__).parent / "fixtures" / "zavitsanos2025_structure_excerpt.txt"
SAMPLE_PDF = Path(__file__).resolve().parents[1] / "sample" / "zavitsanos2025positive-unlabeled.pdf"


def labelled(text):
    return [(s.label, s.heading, s.confidence) for s in detect_sections(text)]


def test_pdf_noise_is_not_mistaken_for_headings():
    text = EXCERPT.read_text(encoding="utf-8")
    headings = {s.heading for s in detect_sections(text)}
    for noise in ("ACM ISBN 979-8-4007-2220-2/25/11", "HDT PU 0.0319 0.0306 0.0377 0.0321", "HRF PU 0.1342 0.0920 0.1822 0.1281",
                  "# Misstatements", "# Serial Misstatements", "EBIT", "CIK", "Years", "AP"):
        assert noise not in headings, noise


def test_front_matter_footer_does_not_cut_the_introduction():
    text = EXCERPT.read_text(encoding="utf-8")
    intro = next(s for s in detect_sections(text) if s.label == "introduction")
    body = text[intro.char_start:intro.char_end]
    assert "ACM ISBN" in body and "resubmissions of annual financial reports" in body


def test_structure_of_the_paper_is_recovered():
    got = labelled(EXCERPT.read_text(encoding="utf-8"))
    assert [(label, heading) for label, heading, _ in got if heading] == [
        ("abstract", "Abstract"), ("introduction", "1 Introduction"), ("theory_hypotheses", "2 Background and related work"),
        ("theory_hypotheses", "2.1 Machine learning for misstatement"), ("theory_hypotheses", "2.2 PU learning"),
        ("other", "3 Misstatements under the PU learning"), ("method", "4 Experimental setup"),
        ("results", "4.1 Experimental results"), ("method", "4.2 Per year analysis"),
        ("discussion_conclusion", "5 Conclusions"), ("references", "References")]


def test_subsections_inherit_the_label_of_their_section_and_say_so():
    secs = {s.heading: s for s in detect_sections(EXCERPT.read_text(encoding="utf-8")) if s.heading}
    sub = secs["2.1 Machine learning for misstatement"]
    assert (sub.label, sub.confidence, sub.level, sub.inherited_from) == ("theory_hypotheses", "inherited", 2, "2 Background and related work")
    own = secs["4.1 Experimental results"]
    assert (own.label, own.confidence, own.inherited_from) == ("results", "high", None)  # its own wording wins
    assert secs["3 Misstatements under the PU learning"].label == "other"  # no label matches and there is no parent
    assert secs["2 Background and related work"].level == 1


def test_sections_tile_the_text_and_carry_their_level():
    text = EXCERPT.read_text(encoding="utf-8")
    secs = detect_sections(text)
    assert "".join(text[s.char_start:s.char_end] for s in secs) == text
    assert all(a.char_end == b.char_start for a, b in zip(secs, secs[1:]))
    assert all("level" in s.to_dict() and "inherited_from" in s.to_dict() for s in secs)


def test_dimension_reading_includes_inherited_subsections():
    secs = detect_sections(EXCERPT.read_text(encoding="utf-8"))
    assert [s.heading for s in sections_for_dimension(secs, "theory")] == [
        "1 Introduction", "2 Background and related work", "2.1 Machine learning for misstatement", "2.2 PU learning"]
    assert [s.heading for s in sections_for_dimension(secs, "design")] == ["4 Experimental setup", "4.2 Per year analysis"]


def test_a_hash_after_a_blank_line_is_still_a_markdown_heading():
    assert [s.label for s in detect_sections("Preface text.\n\n# Introduction\nBody.\n\n## Data and Sample\nMore.\n")] == [
        "preamble", "introduction", "data_sample"]
    assert [s.heading for s in detect_sections("100Counts\n# Misstatements\nBody.\n")] == [""]  # not after a blank line


def test_all_caps_needs_two_words_and_no_digits():
    assert [s.heading for s in detect_sections("Intro text.\nEMPIRICAL RESULTS\nBody.\nEBIT\nmore\nTABLE 2 RESULTS\nx\n") if s.heading] == ["EMPIRICAL RESULTS"]


@pytest.mark.skipif(not SAMPLE_PDF.exists(), reason="the pilot paper is not in this checkout (sample/ is not committed)")
def test_the_whole_real_paper_is_segmented_into_its_own_structure():
    pypdf = pytest.importorskip("pypdf")
    text = "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(str(SAMPLE_PDF)).pages)
    secs = detect_sections(text)
    assert "".join(text[s.char_start:s.char_end] for s in secs) == text
    labels = [s.label for s in secs if s.heading]
    assert labels[:3] == ["abstract", "introduction", "theory_hypotheses"] and "results" in labels and "references" in labels
    assert "ACM ISBN" not in " ".join(s.heading for s in secs)
