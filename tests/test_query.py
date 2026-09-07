"""Gmail query compiler + relaxation ladder (offline)."""

from __future__ import annotations

from hermes_ops.query import FindParams, compile_query, relaxation_plan


def test_subject_or_group_and_domain_sender():
    q = compile_query(
        FindParams(sender="example.edu", subject_terms=["parcial", "entrega"])
    )
    assert "from:example.edu" in q
    assert "subject:(parcial OR entrega)" in q


def test_single_subject_term_no_parens():
    assert compile_query(FindParams(subject_terms=["parcial"])) == "subject:parcial"


def test_whitespace_terms_quoted():
    q = compile_query(FindParams(subject_terms=["fecha limite"]))
    assert 'subject:"fecha limite"' == q


def test_relative_and_iso_dates():
    assert "newer_than:7d" in compile_query(FindParams(after="7d"))
    assert "after:2026/09/01" in compile_query(FindParams(after="2026-09-01"))


def test_filename_ext_implies_has_attachment():
    q = compile_query(FindParams(filename_ext="ics"))
    assert "filename:ics" in q and "has:attachment" in q


def test_label_and_unread():
    q = compile_query(FindParams(label="Hermes/Action", unread_only=True))
    assert 'label:"Hermes/Action"' in q and "is:unread" in q


def test_relaxation_ladder_order():
    p = FindParams(
        sender="x@y.com",
        subject_terms=["swarm"],
        body_terms=["deadline"],
        after="7d",
    )
    plan = relaxation_plan(p)
    notes = [note for _, note in plan]
    assert notes[0] is None
    assert "dropped body_terms" in notes
    assert any("widened date window" in (n or "") for n in notes)
    assert any("full-text" in (n or "") for n in notes)
    # window widening quadruples 7d -> 28d
    assert any("newer_than:28d" in q for q, _ in plan)
    assert any("after=28d" in (n or "") for n in notes)


def test_relaxation_unset_window_goes_90d():
    plan = relaxation_plan(FindParams(subject_terms=["x"]))
    assert any("after:" not in q and "newer_than:90d" in q for q, _ in plan)
