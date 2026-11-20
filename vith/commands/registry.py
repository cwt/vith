"""
Command and `:set` option registration tables, lookup helpers, and decorators.
"""

from __future__ import annotations

from collections.abc import KeysView
from typing import Any, Callable

COMMANDS_TO_HANDLERS: dict[str, Any] = {}  # Global mapping Vi commands to their handler.
COMMANDS_TAKING_LOCATIONS: set[str] = set()  # Name of commands that accept locations.
SET_COMMANDS: dict[str, Any] = {}  # Mapping ':set'-commands to their handler.
SET_COMMANDS_TAKING_VALUE: set[str] = set()


def has_command_handler(command: str | None) -> bool:
    return command in COMMANDS_TO_HANDLERS


def call_command_handler(command: str, editor: Any, variables: Any) -> None:
    """
    Execute command.
    """
    COMMANDS_TO_HANDLERS[command](editor, variables)


def get_commands() -> KeysView[str]:
    return COMMANDS_TO_HANDLERS.keys()


def get_commands_taking_locations() -> set[str]:
    return COMMANDS_TAKING_LOCATIONS


def _cmd(name: str) -> Callable[[Any], Any]:
    """
    Base decorator for registering commands in this namespace.
    """

    def decorator(func: Any) -> Any:
        COMMANDS_TO_HANDLERS[name] = func
        return func

    return decorator


def _unwrap_command(func: Any) -> Any:
    """
    Return the base command function for a possibly already-wrapped handler.
    """
    return getattr(func, '_vith_wrapped_command', func)


def location_cmd(name: str, accepts_force: bool = False) -> Callable[[Any], Any]:
    """
    Decorator that registers a command that takes a location as (optional)
    parameter.
    """
    COMMANDS_TAKING_LOCATIONS.add(name)

    def decorator(func: Any) -> Any:
        base = _unwrap_command(func)

        def command_wrapper(editor: Any, variables: Any) -> None:
            location = variables.get('location')
            force = bool(variables['force'])

            if force and not accepts_force:
                editor.show_message('No ! allowed')
            elif accepts_force:
                base(editor, location, force=force)
            else:
                base(editor, location)

        setattr(command_wrapper, '_vith_wrapped_command', base)
        COMMANDS_TO_HANDLERS[name] = command_wrapper
        # The base function is returned so the module-local alias keeps the
        # original signature (`write_and_quit` still calls `write(...)`);
        # stacked decorators normalize on `base` via `_unwrap_command`.
        return func

    return decorator


def cmd(name: str, accepts_force: bool = False) -> Callable[[Any], Any]:
    """
    Decorator that registers a command that doesn't take any parameters.
    """

    def decorator(func: Any) -> Any:
        base = _unwrap_command(func)

        def command_wrapper(editor: Any, variables: Any) -> None:
            force = bool(variables['force'])

            if force and not accepts_force:
                editor.show_message('No ! allowed')
            elif accepts_force:
                base(editor, force=force)
            else:
                base(editor)

        setattr(command_wrapper, '_vith_wrapped_command', base)
        COMMANDS_TO_HANDLERS[name] = command_wrapper
        # See `location_cmd`: the base is returned for module-local calls;
        # stacks bind the unwrapped base (docs/bugs/075.md).
        return func

    return decorator


def set_cmd(name: str, accepts_value: bool = False) -> Callable[[Any], Any]:
    """
    Decorator that registers a ':set'-command.
    """

    def decorator(func: Any) -> Any:
        SET_COMMANDS[name] = func
        if accepts_value:
            SET_COMMANDS_TAKING_VALUE.add(name)
        return func

    return decorator
