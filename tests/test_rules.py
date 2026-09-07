"""rules-v3 deterministic classifier (offline)."""

from __future__ import annotations

import pytest

from hermes_ops.policy.rules import classify

POLICY = {
    "senders": {
        "important_domains": ["example.edu", "partner.example"],
        "collaborators": ["jordan"],
    },
    "keywords": {
        "action": ["deadline", "invoice", "entrega"],
        "noise": ["newsletter", "unsubscribe", "promocion"],
    },
}


def v(**h):
    return classify(h, POLICY)


def test_noise_keyword_wins_first():
    assert v(subject="Weekly newsletter", **{"from": "prof@example.edu"}).category == "noise"


def test_list_unsubscribe_header_is_noise():
    r = v(subject="Hi", **{"from": "x@corp.com", "list-unsubscribe": "<mailto:u@corp.com>"})
    assert r.category == "noise" and r.confidence >= 0.9


def test_no_reply_sender_is_noise_lower_conf():
    r = v(subject="Receipt", **{"from": "no-reply@service.com"})
    assert r.category == "noise" and r.confidence == 0.8


def test_action_keyword():
    assert v(subject="Invoice due Friday", **{"from": "someone@mail.example"}).category == "action"


def test_important_domain_is_action():
    assert v(subject="hola", **{"from": "decano@example.edu"}).category == "action"


def test_collaborator_is_action():
    assert v(subject="re: idea", **{"from": "jordan@somewhere.example"}).category == "action"


def test_default_is_low_confidence_fyi():
    r = v(subject="lunch?", **{"from": "friend@gmail.com"})
    assert r.category == "fyi" and r.confidence < 0.5


@pytest.mark.parametrize("frm", ["notifications@github.com", "notification@x.io"])
def test_notification_sender_is_fyi(frm):
    assert v(subject="[repo] new issue", **{"from": frm}).category == "fyi"
