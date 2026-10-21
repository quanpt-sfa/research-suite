"""Constants of the readable-export contract (M5 export contract, Amendment 1 A9, WP2)."""
from __future__ import annotations

EXPORTER_VERSION = "1"
QUOTE_MAX = 300          # characters of a quote that may leave the store; never a full text
VALUE_MAX = 400          # characters of a model-written value or statement shown in a table cell
SECRET_ENV_NAMES = ("RESEARCH_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")

# provenance classes: what kind of statement a row is (never a quality judgement)
DATA_DERIVED = "data_derived"                # computed from stored data or runs, deterministic, or a verbatim verified quote
MODEL_SYNTHESISED = "model_synthesised"      # text a model produced, not confirmed by the researcher for this exact text
RESEARCHER_CONFIRMED = "researcher_confirmed"  # the researcher's own words, or a researcher confirmation exists for this exact text
CLASSES = (DATA_DERIVED, MODEL_SYNTHESISED, RESEARCHER_CONFIRMED)

NOT_AVAILABLE = "NOT_AVAILABLE"
MANIFEST_NAME = "manifest.json"      # deterministic: same closure, same bytes
INFO_NAME = "export_info.json"       # variable: time, tool versions, global state digest (excluded from reproducibility)
