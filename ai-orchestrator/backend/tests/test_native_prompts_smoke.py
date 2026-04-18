"""Smoke tests for the native prompt library (Phase 8.5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from native_prompts import NativePromptLibrary


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


@pytest.fixture
def lib_dir(tmp_path: Path) -> Path:
    d = tmp_path / "prompts"
    _write(d / "no_args.yaml", "name: no_args\ndescription: hi\ntemplate: |\n  Hello world.\n")
    _write(
        d / "with_optional.yaml",
        "name: with_optional\n"
        "description: optional arg\n"
        "arguments:\n"
        "  - name: focus\n"
        "    description: optional area\n"
        "    required: false\n"
        "template: |\n"
        "  Audit the home. {focus}\n",
    )
    _write(
        d / "with_required.yaml",
        "name: with_required\n"
        "arguments:\n"
        "  - name: target\n"
        "    required: true\n"
        "template: |\n"
        "  Target is {target}.\n",
    )
    return d


def test_loads_all_yaml_files(lib_dir: Path):
    lib = NativePromptLibrary(lib_dir)
    names = [s.name for s in lib.list()]
    assert names == ["no_args", "with_optional", "with_required"]


def test_renders_template_without_args(lib_dir: Path):
    lib = NativePromptLibrary(lib_dir)
    out = lib.render("no_args", {})
    assert out["ok"] is True
    assert out["text"] == "Hello world."
    assert out["source"] == "native"
    assert out["messages"][0] == {"role": "user", "content": "Hello world."}


def test_optional_arg_renders_empty_when_missing(lib_dir: Path):
    lib = NativePromptLibrary(lib_dir)
    out = lib.render("with_optional", {})
    assert out["ok"] is True
    assert "Audit the home." in out["text"]
    # Trailing whitespace from the missing placeholder is OK.


def test_optional_arg_renders_value_when_present(lib_dir: Path):
    lib = NativePromptLibrary(lib_dir)
    out = lib.render("with_optional", {"focus": "kitchen"})
    assert "kitchen" in out["text"]


def test_required_arg_missing_returns_error(lib_dir: Path):
    lib = NativePromptLibrary(lib_dir)
    out = lib.render("with_required", {})
    assert out["ok"] is False
    assert "missing_required" in out["error"]
    assert "target" in out["error"]


def test_required_arg_present_renders(lib_dir: Path):
    lib = NativePromptLibrary(lib_dir)
    out = lib.render("with_required", {"target": "lights"})
    assert out["ok"] is True
    assert "Target is lights." in out["text"]


def test_unknown_prompt_returns_error(lib_dir: Path):
    lib = NativePromptLibrary(lib_dir)
    out = lib.render("nope", {})
    assert out["ok"] is False
    assert "unknown_prompt" in out["error"]


def test_malformed_yaml_is_skipped(tmp_path: Path):
    d = tmp_path / "p"
    _write(d / "good.yaml", "name: good\ntemplate: ok\n")
    _write(d / "bad.yaml", "name: bad\n")  # no template
    _write(d / "broken.yaml", "::: not yaml :::")
    lib = NativePromptLibrary(d)
    names = [s.name for s in lib.list()]
    assert names == ["good"]


def test_multiple_dirs_merge_with_later_overriding(tmp_path: Path):
    d1 = tmp_path / "a"
    d2 = tmp_path / "b"
    _write(d1 / "foo.yaml", "name: foo\ntemplate: from_a\n")
    _write(d2 / "foo.yaml", "name: foo\ntemplate: from_b\n")
    lib = NativePromptLibrary(d1, d2)
    out = lib.render("foo", {})
    assert out["text"] == "from_b"


def test_missing_dir_does_not_crash(tmp_path: Path):
    lib = NativePromptLibrary(tmp_path / "does_not_exist")
    assert lib.list() == []


def test_to_dict_shape(lib_dir: Path):
    lib = NativePromptLibrary(lib_dir)
    spec = lib.get("with_optional")
    assert spec is not None
    d = spec.to_dict()
    assert d["name"] == "with_optional"
    assert d["source"] == "native"
    assert d["arguments"][0]["name"] == "focus"
    assert d["arguments"][0]["required"] is False


def test_builtin_prompts_load():
    """The five YAMLs shipped with the add-on must load cleanly."""
    builtin = Path(__file__).resolve().parent.parent / "prompts"
    lib = NativePromptLibrary(builtin)
    names = {s.name for s in lib.list()}
    assert {"home_audit", "energy_optimizer", "security_check",
            "morning_routine", "nightly_review"}.issubset(names)
    # Every built-in must render with no args (optionals only).
    for name in ("home_audit", "energy_optimizer", "security_check",
                 "morning_routine", "nightly_review"):
        out = lib.render(name, {})
        assert out["ok"] is True, f"{name} failed: {out}"
        assert out["text"]
