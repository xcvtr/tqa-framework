"""Action registry — register callable actions, resolve by name."""

from __future__ import annotations

from typing import Callable

from tqa_framework.strategy_engine.models import Signal

# fn signature: fn(symbol, price, config, state) -> list[Signal]
ActionFn = Callable[..., list[Signal]]

_registry: dict[str, ActionFn] = {}


def register_action(name: str, fn: ActionFn) -> None:
    """Register an action function by name."""
    if name in _registry:
        raise ValueError(f"Action '{name}' already registered")
    _registry[name] = fn


def get_action(name: str) -> ActionFn:
    """Look up a registered action by name."""
    if name not in _registry:
        raise KeyError(f"Unknown action: '{name}'")
    return _registry[name]


def list_actions() -> list[str]:
    """Return all registered action names."""
    return sorted(_registry.keys())


# ─── Built-in actions ────────────────────────────────────────────────────

def _open_long(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return [
        Signal(
            signal_id=state.get("signal_id", "?"),
            action="open_long",
            direction="LONG",
            price=price,
            timestamp=state.get("ts", ""),
            params=config,
            reason=state.get("reason", ""),
        )
    ]


def _open_short(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return [
        Signal(
            signal_id=state.get("signal_id", "?"),
            action="open_short",
            direction="SHORT",
            price=price,
            timestamp=state.get("ts", ""),
            params=config,
            reason=state.get("reason", ""),
        )
    ]


def _close_long(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return [
        Signal(
            signal_id=state.get("signal_id", "?"),
            action="close_long",
            direction="CLOSE",
            price=price,
            timestamp=state.get("ts", ""),
            params=config,
            reason=state.get("reason", ""),
        )
    ]


def _close_short(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return [
        Signal(
            signal_id=state.get("signal_id", "?"),
            action="close_short",
            direction="CLOSE",
            price=price,
            timestamp=state.get("ts", ""),
            params=config,
            reason=state.get("reason", ""),
        )
    ]


def _close_all(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return [
        Signal(
            signal_id=state.get("signal_id", "?"),
            action="close_all",
            direction="CLOSE",
            price=price,
            timestamp=state.get("ts", ""),
            params=config,
            reason=state.get("reason", ""),
        )
    ]


def _noop(symbol: str, price: float, config: dict, state: dict) -> list[Signal]:
    return []


# Register built-in actions
_builtins = [
    ("open_long", _open_long),
    ("open_short", _open_short),
    ("close_long", _close_long),
    ("close_short", _close_short),
    ("close_all", _close_all),
    ("noop", _noop),
]
for _name, _fn in _builtins:
    _registry[_name] = _fn