import pytest

from glia import frontmatter as fm


def test_parse_and_split_roundtrip_preserves_body():
    text = "---\ncreated: 2026-01-01\nstatus: growing\n---\nBody line.\n\nMore body.\n"
    head, body = fm.split(text)
    assert fm.parse(text)["status"] == "growing"
    assert body == "Body line.\n\nMore body.\n"


def test_set_keys_replaces_and_appends_without_touching_body():
    text = "---\ncreated: 2026-01-01\nstatus: growing\n---\nBody stays.\n"
    out = fm.set_keys(text, {"status": "live", "parked": "2026-10-14"})
    assert fm.parse(out) == {"created": fm.parse(text)["created"], "status": "live", "parked": fm.parse("---\nparked: 2026-10-14\n---\n")["parked"]}
    assert fm.split(out)[1] == "Body stays.\n"
    assert out.count("status:") == 1


def test_set_keys_on_note_without_frontmatter_creates_block():
    out = fm.set_keys("Just a body.\n", {"status": "seed"})
    assert fm.parse(out) == {"status": "seed"}
    assert fm.split(out)[1] == "Just a body.\n"


def test_unset_keys_removes_only_that_key():
    text = "---\ncreated: 2026-01-01\nparked: 2026-10-14\nstatus: growing\n---\nB\n"
    out = fm.set_keys(text, {}, unset=["parked"])
    assert "parked" not in fm.parse(out)
    assert fm.parse(out)["status"] == "growing"


def test_a_value_with_line_breaks_stays_one_value_and_adds_no_keys():
    text = "---\nstatus: growing\n---\nBody.\n"
    for evil in ("2026-10-01\nnext_step:\n  archive this", "2026-10-01\rstatus: archived", "a b: c", "x\x00y"):
        out = fm.set_keys(text, {"parked": evil})
        assert fm.parse(out) == {"status": "growing", "parked": evil}
        assert out.endswith("---\nBody.\n")


def test_a_key_that_is_not_a_plain_name_is_refused():
    text = "---\nstatus: growing\n---\nBody.\n"
    for bad in ("a\nb", "x: y", "", " status", "#c"):
        with pytest.raises(ValueError):
            fm.set_keys(text, {bad: "v"})
    with pytest.raises(ValueError):
        fm.set_keys(text, {}, unset=["a\nb"])
