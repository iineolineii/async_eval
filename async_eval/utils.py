import ast
import builtins
import re
import sys
from contextlib import contextmanager
from copy import deepcopy
from types import TracebackType
from typing import Any, Callable, ContextManager


uuid4match = r"[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}"
filename_pattern = re.compile(rf"\<aeval \d+? ({uuid4match})\>")


@contextmanager
def custom_excepthook(
    handler: Callable[[type[BaseException], BaseException, TracebackType], Any]
):
    """
    Context manager that allows temporary replace
    sys.excepthook with the given `handler` function.
    """
    original_excepthook = sys.excepthook

    def wrapper(*args):
        try:
            handler(*args)
        finally:
            sys.excepthook = original_excepthook

    sys.excepthook = wrapper
    yield

class CustomBuiltins(ContextManager):
    def __init__(self, extensions: dict[str, Any] | None = None, replace: bool = False):
        """
        Context manager that allows temporary
        extend or replace (not safe!) builtin variable scope
        with the given `extensions` dictionary.
        """
        if extensions is None:
            extensions = {}
        self.extensions = extensions or {}
        self.orig_builtins: dict[str, Any] = {}
        self.replace = replace

    def __enter__(self):
        # Save the original builtins
        self.orig_builtins = deepcopy(builtins.__dict__)

        # Replace builtins with the provided extensions
        if self.replace:
            builtins.__dict__.clear()
        builtins.__dict__.update(self.extensions)

        return builtins

    def __exit__(self, exc_type, exc_value, traceback):
        # Restore the original builtins
        builtins.__dict__.clear()
        builtins.__dict__.update(self.orig_builtins)

        # Propagate any exceptions
        return False

custom_builtins = CustomBuiltins


def reconstruct_node(
    node: ast.AST,
    excluded_names_pattern: str | re.Pattern | None = r"^(?:parent|lineno|end_lineno|col_offset|end_col_offset)$",
    indentsize: int = 4,
    show_full_names: bool = True,
    show_None_attrs: bool = False,
    *,
    depth: int = 1
) -> str:
    indent = depth * (indentsize * " ")
    result = []

    for name, attr in vars(node).items():
        if (
            excluded_names_pattern and re.search(excluded_names_pattern, name)
            or name == "parent" # Prevent child-parent recursion
        ):
            continue

        if isinstance(attr, list) and attr:
            attr = [reconstruct_node(subnode, excluded_names_pattern, depth=depth+2) for subnode in attr]
            attr = f",\n".join(attr)
            attr = f"[\n{attr}\n{indent}]"

        elif isinstance(attr, ast.AST):
            attr = reconstruct_node(attr, excluded_names_pattern, depth=depth+1)

        elif isinstance(attr, str):
            attr = repr(attr)

        if show_None_attrs or attr is not None:
            result.append(f"{name}={str(attr).lstrip()}")

    result = f",\n{indent}".join(result)

    if result:
        result = f"\n{indent}{result}\n{(depth-1) * (indentsize * ' ')}"

    if not show_full_names:
        cls_name = type(node).__name__
    elif hasattr(type(node), "__module__"):
        cls_name = ".".join([type(node).__module__, type(node).__qualname__])
    else:
        cls_name = type(node).__qualname__

    result = f"{(depth-1) * "    "}{cls_name}({result})"
    return result

def dump_node(
    node: ast.AST,
    exclude_pattern: re.Pattern | str = "parent"
):
    return {
        "_": ".".join([type(node).__module__, type(node).__qualname__])
        if hasattr(type(node), "__module__") else type(node).__qualname__,
    **{
            name: (
                dump_node(attr) if isinstance(attr, ast.AST)
                else [
                    dump_node(subnode)
                    for subnode in attr
                ] if isinstance(attr, list)
                else attr
            )
            for name, attr in getattr(node, "__dict__", {}).items()
            if (
                not re.search(exclude_pattern, name)
                or name != "parent" # Prevent child-parent recursion
            )
        }
    }


def extract_pointers(traceback_text: str) -> dict[tuple[str, int, str], str]:
    pattern = r'  File "(.*?)", line (\d+), in (.*?)\n    .*?\n(    [~^]+)'
    matches: list[str] = re.findall(pattern, traceback_text)

    return {
        (filename, int(lineno), name): pointer
        for filename, lineno, name, pointer in matches
    }
