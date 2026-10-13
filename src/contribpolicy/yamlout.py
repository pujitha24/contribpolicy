"""A tiny YAML emitter for the policy document (no PyYAML dependency).

Strings are always double-quoted using JSON escaping, which is valid YAML
1.2 double-quoted style. Lists of scalars are written in flow style
(``["a", "b"]``); everything else in block style.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["dump"]


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v), ensure_ascii=False)


def _is_scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, bool, int, float))


def _flow(v: list[Any]) -> str:
    return "[" + ", ".join(_scalar(x) for x in v) + "]"


def _emit(obj: Any, indent: int, out: list[str]) -> None:
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            out.append(pad + "{}")
            return
        for k, v in obj.items():
            key = k if isinstance(k, str) and k.replace("_", "").replace("-", "").isalnum() else _scalar(k)
            if _is_scalar(v):
                out.append(f"{pad}{key}: {_scalar(v)}")
            elif isinstance(v, list) and (not v or all(_is_scalar(x) for x in v)):
                out.append(f"{pad}{key}: {_flow(v)}")
            elif isinstance(v, dict) and not v:
                out.append(f"{pad}{key}: {{}}")
            else:
                out.append(f"{pad}{key}:")
                _emit(v, indent + 1, out)
        return
    if isinstance(obj, list):
        for item in obj:
            if _is_scalar(item):
                out.append(f"{pad}- {_scalar(item)}")
            elif isinstance(item, list) and all(_is_scalar(x) for x in item):
                out.append(f"{pad}- {_flow(item)}")
            elif isinstance(item, dict) and item:
                sub: list[str] = []
                _emit(item, indent + 1, sub)
                first = sub[0][len("  " * (indent + 1)) :]
                out.append(f"{pad}- {first}")
                out.extend(sub[1:])
            else:
                sub = []
                _emit(item, indent + 1, sub)
                out.append(f"{pad}-")
                out.extend(sub)
        return
    out.append(pad + _scalar(obj))


def dump(obj: Any) -> str:
    out: list[str] = []
    _emit(obj, 0, out)
    return "\n".join(out) + "\n"
