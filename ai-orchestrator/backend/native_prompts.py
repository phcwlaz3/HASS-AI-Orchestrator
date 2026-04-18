"""Built-in prompt library for the deep reasoning agent.

Phase 8.5: ships a default set of "workflow" prompts so the dashboard
prompt library is useful out of the box, with no external MCP server
required. Prompts are loaded from ``prompts/*.yaml`` shipped alongside
this module; the operator can drop additional ``.yaml`` files into a
configurable directory at runtime to extend the catalog.

Each prompt file looks like::

    name: home_audit
    description: Quick read of the whole house.
    arguments:
      - name: focus
        description: Optional area to zoom in on.
        required: false
    template: |
      Audit the current state of the home.
      {focus_clause}

Templates use Python ``str.format``-style placeholders. Argument
values that are missing are substituted with empty strings; convention
is to wrap optional arguments in a ``{name_clause}`` that the prompt
author sets via a small render helper if needed (today: just plain
``{name}`` substitution).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class NativePromptSpec:
    name: str
    description: str = ""
    arguments: List[Dict[str, Any]] = field(default_factory=list)
    template: str = ""
    source: str = "native"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": list(self.arguments),
            "source": self.source,
        }


class NativePromptLibrary:
    """Loads built-in prompts from one or more directories."""

    def __init__(self, *dirs: str | Path) -> None:
        self.dirs: List[Path] = [Path(d) for d in dirs if d]
        self._prompts: Dict[str, NativePromptSpec] = {}
        self.reload()

    # ------------------------------------------------------------------
    def reload(self) -> int:
        self._prompts.clear()
        for d in self.dirs:
            if not d.exists() or not d.is_dir():
                continue
            for path in sorted(d.glob("*.yaml")):
                try:
                    spec = self._load_file(path)
                    if spec.name in self._prompts:
                        logger.warning(
                            "Native prompt %r from %s shadows earlier definition",
                            spec.name, path,
                        )
                    self._prompts[spec.name] = spec
                except Exception as exc:
                    logger.warning("Failed to load prompt %s: %s", path, exc)
        return len(self._prompts)

    def _load_file(self, path: Path) -> NativePromptSpec:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("prompt file must be a mapping")
        name = (data.get("name") or path.stem).strip()
        if not name:
            raise ValueError("prompt name is required")
        description = (data.get("description") or "").strip()
        template = (data.get("template") or "").strip()
        if not template:
            raise ValueError(f"prompt {name!r} has no template")
        arguments = data.get("arguments") or []
        if not isinstance(arguments, list):
            raise ValueError("arguments must be a list")
        # Normalise argument shape so the API surface is stable.
        norm_args: List[Dict[str, Any]] = []
        for a in arguments:
            if not isinstance(a, dict):
                continue
            norm_args.append({
                "name": str(a.get("name") or "").strip(),
                "description": str(a.get("description") or "").strip(),
                "required": bool(a.get("required", False)),
            })
        return NativePromptSpec(
            name=name,
            description=description,
            arguments=norm_args,
            template=template,
            source="native",
        )

    # ------------------------------------------------------------------
    def list(self) -> List[NativePromptSpec]:
        return sorted(self._prompts.values(), key=lambda p: p.name)

    def get(self, name: str) -> Optional[NativePromptSpec]:
        return self._prompts.get(name)

    def render(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        spec = self.get(name)
        if spec is None:
            return {"ok": False, "error": f"unknown_prompt:{name}"}
        args = arguments or {}
        # Validate required arguments.
        missing = [a["name"] for a in spec.arguments
                   if a.get("required") and not str(args.get(a["name"], "")).strip()]
        if missing:
            return {"ok": False, "error": f"missing_required:{','.join(missing)}"}
        # Substitute. Missing optional args become empty strings rather
        # than KeyError so prompt authors don't have to defend against
        # unset optionals.
        class _SafeDict(dict):
            def __missing__(self, key):
                return ""
        try:
            text = spec.template.format_map(_SafeDict(args))
        except Exception as exc:
            return {"ok": False, "error": f"render_error:{exc}"}
        return {
            "ok": True,
            "name": spec.name,
            "description": spec.description,
            "text": text.strip(),
            "messages": [{"role": "user", "content": text.strip()}],
            "source": spec.source,
        }
