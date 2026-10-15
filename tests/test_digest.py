import json

from glia.digest import render


def sample():
    return json.loads(open("fixtures/sample-run.json").read())


def test_digest_has_action_target_reason_revert_placeholder_and_tick_slot_per_item():
    run = sample()
    text, meta = render(run)
    for it in run["items"]:
        assert it["action"] in text and it["target"] in text and it["reason"] in text
    assert text.count("✅") >= len([i for i in run["items"] if i["ladder"]["tier"] != "never"])
    assert text.count("git revert <sha-") >= 1
    assert meta["items"] == len(run["items"]) and meta["posted"] is False


def test_digest_chunks_fit_discord_limit():
    text, meta = render(sample())
    assert all(len(c) <= 2000 for c in meta["chunks"])
    assert "".join(meta["chunks"]).replace("\n", "") == text.replace("\n", "")


def test_never_tier_items_show_as_not_tickable():
    text, _ = render(sample())
    assert "⛔" in text and "not tick-able" in text


def test_digest_names_the_proposer_and_mode():
    text, _ = render(sample())
    assert "example-model" in text and "propose-only" in text


def test_digest_renders_free_form_propose_with_its_text():
    run = sample()
    run["items"].append({"id": "p-free0001", "action": "propose", "target": "Tasks/cluster-pickup.md", "reason": "note says superseded",
                         "args": {"what": "Consider closing (status: done) and archiving"},
                         "ladder": {"tier": "ask", "open_tasks": [], "flags": ["free-form"]}, "status": "proposed"})
    text, _ = render(run)
    assert "**propose** `Tasks/cluster-pickup.md` → Consider closing (status: done) and archiving" in text
