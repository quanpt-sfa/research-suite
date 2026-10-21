"""Quote matching tolerates what PDF extraction and models change without changing the words, and nothing else."""
from __future__ import annotations

import pytest

from research_harness.workflows import quotes

TEXT = ("The estimator is robust.\nWe note that no hyper-\nparameter tuning was conducted in this study. The F1- score for “unseen” firms is zero—even\n"
        "after the ﬁrst re-estimation. Serial mis- statements can inﬂate results. See Elkan-\nNoto [14].")


@pytest.mark.parametrize("quote", [
    "no hyperparameter tuning was conducted in this study",            # hyphenated at a line end in the text, whole in the quote
    "no hyper- parameter tuning was conducted",                          # the text's own form
    "The F1-score for \"unseen\" firms is zero",                         # a compound broken across a line; straight quotes for curly
    "zero-even after the first re-estimation",                           # em dash and a ligature
    "zero—even after the ﬁrst reestimation",
    "Serial misstatements can inflate results",                          # ligature fl in the text
    "See ElkanNoto [14]",                                                # a hyphen anywhere is not significant
    "See Elkan-Noto [14].",
    "See Elkan-Noto [ 14 ].",                                            # space just inside brackets (PDF citation spacing)
])
def test_the_same_words_match_whatever_the_hyphens_quotes_and_ligatures(quote):
    span = quotes.find_quote(TEXT, quote)
    assert span is not None, quote
    start, end = span
    assert quotes.verify_span(TEXT, start, end, quote).ok and TEXT[start:end].strip() == TEXT[start:end]


def test_the_span_maps_back_to_the_original_text():
    start, end = quotes.find_quote(TEXT, "no hyperparameter tuning was conducted in this study")
    assert TEXT[start:end] == "no hyper-\nparameter tuning was conducted in this study"
    start, end = quotes.find_quote(TEXT, "Serial misstatements can inflate results")
    assert TEXT[start:end] == "Serial mis- statements can inﬂate results"


@pytest.mark.parametrize("quote", [
    "no hyperparameter tuning was conducted in these studies",           # a different word
    "No hyperparameter tuning was conducted",                            # case is significant
    "The F2-score for unseen firms",                                     # a digit is significant
    "no hyperparameter tuning was conducted in this study,",             # punctuation is significant
    "We note that hyperparameter tuning",                                 # a missing word
    "the ﬁrst re-estimation of the model",                          # words that are not there
    "",
])
def test_nothing_else_is_loosened(quote):
    assert quotes.find_quote(TEXT, quote) is None


def test_a_wrong_span_is_still_refused():
    start, end = quotes.find_quote(TEXT, "no hyperparameter tuning was conducted in this study")
    assert quotes.verify_span(TEXT, start, end, "no hyperparameter tuning was conducted in this study").ok
    assert not quotes.verify_span(TEXT, start, end, "no hyperparameter tuning was never conducted in this study").ok
    assert not quotes.verify_span(TEXT, start, end + 40, "no hyperparameter tuning was conducted in this study").ok
    assert quotes.verify_span(TEXT, None, None, "after the first re-estimation").ok
