"""The library file: what loads, what is refused at load, and which overrides are accepted."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from gao_idl.service import load_library, resolve_params
from gao_idl.service.casts import always_defect, bid_80_percent, bid_value, tit_for_tat
from gao_idl.service.library import Library

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "examples" / "library" / "library.json"


def _raw() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(LIBRARY.read_text())
    for entry in data["entries"]:  # absolute paths, so the copy can live anywhere
        entry["spec"] = str((LIBRARY.parent / entry["spec"]).resolve())
        entry["cards"] = str((LIBRARY.parent / entry["cards"]).resolve())
    return data


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "library.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture(scope="module")
def library() -> Library:
    return load_library(LIBRARY)


def test_the_shipped_library_loads(library: Library) -> None:
    assert [e.slug for e in library.entries] == ["auction", "prisoners-dilemma", "carbon-market"]
    for entry in library.entries:
        assert len(entry.casts) >= 2
        spec = library.spec_of(entry)
        assert entry.title == spec.title
        assert library.cards_of(entry)
        for p in entry.params:
            assert p.name in spec.params
        for cast in entry.casts:
            assert set(cast.policies) == {r.name for r in spec.roles}
            for ref in cast.policies.values():
                assert callable(library.policy(ref))
    auction = library.entry("auction")
    assert {p.name for p in auction.params} == {"reserve", "rule", "reveal_bids"}
    vcm = library.entry("carbon-market")
    assert {"p_audit", "penalty", "reputation_visible", "rounds"} <= {p.name for p in vcm.params}


def test_unknown_slug_is_a_key_error(library: Library) -> None:
    with pytest.raises(KeyError):
        library.entry("nope")


def test_resolve_params_merges_into_the_spec_params(library: Library) -> None:
    entry = library.entry("auction")
    spec = library.spec_of(entry)
    params = resolve_params(entry, spec, {"reserve": 5, "rule": "first_price"})
    assert params["reserve"] == 5.0 and isinstance(params["reserve"], float)
    assert params["rule"] == "first_price"
    assert params["values"] == spec.params["values"]
    assert spec.params["reserve"] == 2.0  # the spec is not changed
    assert resolve_params(entry, spec, {}) == spec.params


@pytest.mark.parametrize(
    ("overrides", "words"),
    [
        ({"reserve": 99.0}, ["'reserve'", "at most"]),  # out of range
        ({"reserve": -1}, ["'reserve'", "at least"]),
        ({"reserve": "3"}, ["'reserve'", "number"]),  # wrongly typed
        ({"reserve": True}, ["'reserve'", "number"]),
        ({"reserve": float("nan")}, ["'reserve'", "finite"]),
        ({"reveal_bids": 1}, ["'reveal_bids'", "true or false"]),
        ({"rule": "third_price"}, ["'rule'", "one of"]),
        ({"values": {"alice": 1.0}}, ["'values'", "cannot be changed"]),  # undeclared
        ({"env_ref": "os:system"}, ["'env_ref'", "cannot be changed"]),
    ],
)
def test_bad_overrides_are_refused_naming_the_field(
    library: Library, overrides: dict[str, Any], words: list[str]
) -> None:
    entry = library.entry("auction")
    with pytest.raises(ValueError) as e:
        resolve_params(entry, library.spec_of(entry), overrides)
    for word in words:
        assert word in str(e.value)


def test_int_params_refuse_fractions(library: Library) -> None:
    entry = library.entry("prisoners-dilemma")
    spec = library.spec_of(entry)
    with pytest.raises(ValueError, match="'rounds'.*whole"):
        resolve_params(entry, spec, {"rounds": 2.5})
    with pytest.raises(ValueError, match="'rounds'.*at most"):
        resolve_params(entry, spec, {"rounds": 21})
    assert resolve_params(entry, spec, {"rounds": 4})["rounds"] == 4


def test_params_must_be_an_object(library: Library) -> None:
    entry = library.entry("auction")
    with pytest.raises(ValueError, match="object"):
        resolve_params(entry, library.spec_of(entry), [1, 2])  # type: ignore[arg-type]


def test_a_policy_that_does_not_resolve_is_refused_at_load(tmp_path: Path) -> None:
    data = _raw()
    data["entries"][0]["casts"][0]["policies"]["bidder"] = "gao_idl.service.casts:no_such_policy"
    with pytest.raises(ValueError, match="no_such_policy"):
        load_library(_write(tmp_path, data))
    data["entries"][0]["casts"][0]["policies"]["bidder"] = "no_such_module_xyz:policy"
    with pytest.raises(ValueError, match="no_such_module_xyz"):
        load_library(_write(tmp_path, data))
    data["entries"][0]["casts"][0]["policies"]["bidder"] = "not a reference"
    with pytest.raises(ValueError, match="module.path:function"):
        load_library(_write(tmp_path, data))
    assert "no_such_module_xyz" not in sys.modules


def _refused(tmp_path: Path, data: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        load_library(_write(tmp_path, data))


def test_load_checks_slugs(tmp_path: Path) -> None:
    data = _raw()
    data["entries"][1]["slug"] = "auction"
    _refused(tmp_path, data, "used twice")
    data = _raw()
    data["entries"][0]["slug"] = "Auction"
    _refused(tmp_path, data, "lower-case")
    data["entries"][0]["slug"] = "auction/../x"
    _refused(tmp_path, data, "lower-case")


def test_load_checks_files(tmp_path: Path) -> None:
    data = _raw()
    data["entries"][0]["spec"] = "missing.json"
    _refused(tmp_path, data, "cannot load the spec")
    data = _raw()
    data["entries"][0]["cards"] = "missing.json"
    _refused(tmp_path, data, "cannot load the cards")
    data = _raw()
    data["entries"][0]["cards"] = data["entries"][1]["cards"]  # PD cards under the auction spec
    _refused(tmp_path, data, "another environment")
    _refused(tmp_path, {"entries": "x"}, "not valid")


def test_load_checks_params(tmp_path: Path) -> None:
    data = _raw()
    data["entries"][0]["params"][0]["name"] = "not_a_param"
    _refused(tmp_path, data, "not a parameter of the spec")
    data = _raw()
    data["entries"][0]["params"][0]["default"] = 99.0
    _refused(tmp_path, data, "default of parameter 'reserve' must be at most")
    data = _raw()
    data["entries"][0]["params"][1]["default"] = "third_price"
    _refused(tmp_path, data, "default of parameter 'rule'")
    data = _raw()
    data["entries"][0]["params"][1]["choices"] = []
    _refused(tmp_path, data, "no choices")
    data = _raw()
    data["entries"][0]["params"].append(data["entries"][0]["params"][0])
    _refused(tmp_path, data, "declared twice")
    data = _raw()
    data["entries"][0]["params"][0]["kind"] = "str"
    _refused(tmp_path, data, "not valid")
    data = _raw()
    data["entries"][0]["params"][0]["extra"] = 1
    _refused(tmp_path, data, "not valid")


def test_load_checks_casts(tmp_path: Path) -> None:
    data = _raw()
    del data["entries"][1]["casts"][0]["policies"]["col"]
    _refused(tmp_path, data, r"no policy for \['col'\]")
    data = _raw()
    data["entries"][0]["casts"][0]["policies"]["auctioneer"] = "gao_idl.service.casts:bid_value"
    _refused(tmp_path, data, "unknown role kinds")
    data = _raw()
    data["entries"][0]["casts"][1]["name"] = data["entries"][0]["casts"][0]["name"]
    _refused(tmp_path, data, "cast name is used twice")
    data = _raw()
    data["entries"][0]["casts"] = []
    _refused(tmp_path, data, "no casts")


def _request(observation: dict[str, Any]) -> Any:
    from gao_idl.core.schemas import DecisionRequest

    return DecisionRequest(
        decision_id="x#r",  # type: ignore[arg-type]
        path=["x"],
        layer="x",
        role="r",  # type: ignore[arg-type]
        agent_id="a",  # type: ignore[arg-type]
        observation=observation,
        action_schema={},
    )


def test_the_cast_policies() -> None:
    assert bid_value(_request({"value": 10.0}), []) == {"amount": 10.0}
    assert bid_80_percent(_request({"value": 7.0}), []) == {"amount": 5.6}
    assert always_defect(_request({}), []) == {"move": "D"}
    assert tit_for_tat(_request({"own_history": []}), []) == {"move": "C"}
    assert tit_for_tat(_request({"own_history": [["C", "D"]]}), []) == {"move": "D"}
    assert tit_for_tat(_request({"own_history": [["C", "D"], ["D", "C"]]}), []) == {"move": "C"}
