# -*- coding: utf-8 -*-
"""Strict, review-only vocabulary for inspection verdict candidates.

This is lexical postprocessing, not acoustic hotword biasing. sherpa-onnx's
offline Paraformer factory/greedy decoder does not implement decoder hotwords.
Call this only for a selected enumeration with both verdict options. Preserve
the recognizer transcript, its confidence and the manual-confirmation gate.
"""
import re
import unicodedata


TERMS = ("合格", "不合格")
# Whole-payload variants only. Never replace a substring, drop a negative, or
# treat unrelated words such as “合法” as an inspection verdict.
_VARIANTS = {
    "合格": "合格", "何格": "合格", "和格": "合格",
    "不合格": "不合格", "不何格": "不合格", "不和格": "不合格", "布合格": "不合格",
}


def normalize_candidate(text, options=None):
    """Return a bounded lexical candidate, or None for ambiguous content.

All meaningful characters must match a single approved phrase. Surrounding
punctuation and ASR token spaces are ignored; internal punctuation is retained
so “不，合格” and conflicting/uncertain sentences cannot become “合格”.
"""
    if not isinstance(text, str) or not text or len(text) > 40:
        return None
    options = TERMS if options is None else options
    if not isinstance(options, (list, tuple, set, frozenset)) or not all(term in options for term in TERMS):
        return None
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).strip("，,。.!！;；:：")
    value = _VARIANTS.get(compact)
    if value is None:
        return None
    changed = compact != value
    return {"value": value, "original": text, "normalized": compact, "changed": changed,
            "rule": "inspection_verdict_homophone" if changed else "inspection_verdict_exact",
            "note": ("结论词候选：原转写“%s”，按结论词表建议“%s”，请核对原音后确认" % (compact, value))
            if changed else "结论词已识别，请核对后确认"}


def parse_verdict(text, options=None):
    candidate = normalize_candidate(text, options)
    return candidate["value"] if candidate else None


def context(schema, current_key=None):
    """Select verdict labels/options only, never nominal or recorded values."""
    keys = []
    for section in (schema or {}).get("sections", []):
        for field in section.get("fields", []):
            if field.get("derived") or (current_key and field.get("key") != current_key):
                continue
            options = field.get("options") or []
            if field.get("type") == "enum" and all(term in options for term in TERMS):
                key = field.get("key")
                if key and key not in keys:
                    keys.append(key)
    return {"active": bool(keys), "fields": keys, "terms": list(TERMS) if keys else [],
            "mode": "strict_verdict_candidates", "acoustic_biasing": False}
