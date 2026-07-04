"""Tests for the data layer — no API key required (no LLM calls)."""

from __future__ import annotations

from app.data import loaders
from app.data.regulations import load_regulations
from app.data.size_standards import load_size_standards
from app.llm.checks import RETRIEVAL_MAP

REG_PATH = loaders.data_path("title-13-chapter-i.txt")


def test_regulations_parse_and_resolve():
    idx = load_regulations(REG_PATH)
    assert len(idx.chunks) > 1000
    # Messy citations all resolve to the same ineligible-business section.
    for raw in ("13 CFR § 120.110(b)", "§120.110", "120.110", "Sec. 120.110"):
        chunk = idx.resolve(raw)
        assert chunk is not None
        assert chunk.section == "120.110"
    assert idx.resolve("121.201") is not None


def test_size_standards_parse():
    ss = load_size_standards(REG_PATH)
    assert len(ss) > 500
    assert ss["311811"]["type"] == "employees"
    assert ss["423450"]["threshold"] == 200.0


def test_ground_truth_stripped_from_biz_transactions():
    banned = loaders.GROUND_TRUTH_COLUMNS
    for bid in loaders.biz_business_ids():
        for txn in loaders.biz_transactions_safe(bid):
            assert not (set(txn) & banned), f"leak in {bid}: {set(txn) & banned}"


def test_ground_truth_stripped_from_fs_package():
    banned = loaders.GROUND_TRUTH_COLUMNS
    for bid in loaders.fs_business_ids():
        for row in loaders.fs_package_safe(bid):
            assert not (set(row) & banned)


def test_overlay_covers_all_businesses():
    overlay = loaders.load_overlay()
    for bid in loaders.fs_business_ids() + loaders.biz_business_ids():
        assert bid in overlay, f"missing overlay for {bid}"


def test_overlay_naics_codes_exist_in_size_table():
    ss = load_size_standards(REG_PATH)
    for bid, rec in loaders.load_overlay().items():
        assert rec["naics_code"] in ss, f"{bid} naics {rec['naics_code']} not in size table"


def test_every_retrieval_citation_resolves():
    idx = load_regulations(REG_PATH)
    for check_id, citations in RETRIEVAL_MAP.items():
        for c in citations:
            assert idx.resolve(c) is not None, f"{check_id}: {c} did not resolve"
