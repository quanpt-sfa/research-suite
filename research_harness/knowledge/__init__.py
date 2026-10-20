"""Knowledge infrastructure (plan v3, M2): corpus intake, the shared library, its lifecycle and journal policies.

Everything here is written by an authenticated actor through `StateStore.write_tx`. Nothing in this package
turns a model answer or a keyword into a verdict: validity dimensions are computed from recorded facts and
researcher confirmations (invariants I1, I3, I9, I14-I16).
"""
