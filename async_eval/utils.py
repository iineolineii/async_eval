import ast
import re
from copy import copy
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Iterable, NoReturn, final

uuid4match = r"[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}"
filename_pattern = re.compile(rf"\<code ({uuid4match})\>")

def uniquify_name(name: str, namespace: Iterable[str]) -> str:
    """Generate completely unique name based on the old name

    Args:
        name (str): The old name that needs to be changed
        namespace (Iterable[str]): Names that need to be checked for uniqueness

    Returns:
        str: The new unique name

    Examples:
        >>> uniquify_name("foo", {"foo", "bar", "baz"})
        '_foo'
        >>> uniquify_name("bar", {"foo", "bar", "baz"})
        '_bar'
    """
    while name in namespace:
        name = "_" + name

    return name

def extract_pointers(traceback_text: str) -> dict[tuple[str, int, str], str]:
    pattern = r'  File "(.*?)", line (\d+), in (.*?)\n    .*?\n(    [~^]+)'
    matches: list[str] = re.findall(pattern, traceback_text)

    pointers = {
        (filename, int(lineno), name): pointer
        for filename, lineno, name, pointer in matches
    }

    return pointers

class NodeTransformer:
    def transform_module(
        self,
        module: ast.Module
    ) -> ast.Module:

        module = self.patch_returns(module) # Patch all returns outside of any function def
        module.body[-1] = self.patch_statement(module.body[-1])

        #region: Empty result
        glb = ast.copy_location(ast.Call(func=ast.Name(id = "globals", ctx = ast.Load()), args=[], keywords=[]), old_node=module.body[-1])
        loc = ast.copy_location(ast.Call(func=ast.Name(id = "locals", ctx = ast.Load()), args=[], keywords=[]), old_node=module.body[-1])

        patched = ast.Return(value=ast.Tuple(elts=[glb, loc], ctx=ast.Load()))
        patched = ast.copy_location(patched, module.body[-1])
        module.body.append(patched)
        #endregion

        return module

    def patch_returns[AST: ast.AST](
        self,
        node: AST
    ) -> AST:

        if isinstance(node, ast.Return):
            node = self.handle_Return(node)
            return node

        for name, values in ast.iter_fields(node):

            if isinstance(values, list):
                values = [
                    self.patch_returns(value)
                    if not isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef))
                    else value
                    for value in values
                ]

            elif isinstance(values, ast.AST):
                values = self.patch_returns(values)

            setattr(node, name, values)

        return node

    def patch_statement[stmt: ast.stmt](
        self,
        node: stmt
    ) -> stmt | ast.Return:

        old_node = node
        node.end_lineno = getattr(node, "end_lineno", node.lineno)

        if isinstance(node, ast.If):
            node = self.handle_If(node)

        elif isinstance(node, (ast.For, ast.AsyncFor)):
            node = self.handle_For(node)

        elif isinstance(node, ast.Assign):
            node = self.handle_Assign(node)

        elif isinstance(node, ast.AugAssign):
            node = self.handle_AugAssign(node)

        elif isinstance(node, (ast.With, ast.AsyncWith)):
            node = self.handle_With(node)

        elif isinstance(node, ast.Expr):
            node = self.handle_Expr(node)

        elif isinstance(node, (ast.Try, ast.TryStar)):
            node = self.handle_Try(node)

        elif isinstance(node, ast.Match):
            node = self.handle_Match(node)

        elif isinstance(node, ast.TypeAlias):
            node = self.handle_TypeAlias(node)

        node = ast.copy_location(node, old_node)
        return node


    def handle_Return(
        self,
        node: ast.Return
    ) -> ast.Return:
        """Patch a single return. Add globals and locals call to return"s value"""
        value = copy(node.value)

        # NOTE: In theory, this can only happen with for's target node, but who knows...
        if hasattr(value, "ctx") and not isinstance(getattr(node.value, "ctx"), ast.Load):
            # Multi-target loop
            if isinstance(value, ast.Tuple):
                value.elts = [self.change_ctx(elt) for elt in value.elts]

            setattr(value, "ctx", ast.Load())

        glb = ast.copy_location(self.parse_expr("globals()"), node)
        loc = ast.copy_location(self.parse_expr( "locals()"), node)

        value = ast.Tuple(elts=[value, glb, loc], ctx=ast.Load())
        value = ast.copy_location(value, node)

        patched = ast.Return(value=value)
        patched = ast.copy_location(patched, node)

        return patched

    def handle_If(
        self,
        node: ast.If
    ) -> ast.If:
        node.body[-1] = self.patch_statement(node.body[-1])

        if getattr(node, "orelse", None):
            node.orelse[-1] = self.patch_statement(node.orelse[-1])

        return node

    def handle_For[For: ast.For | ast.AsyncFor](
        self,
        node: For
    ) -> For:
        if getattr(node, "orelse", None):
            node.orelse[-1] = self.patch_statement(node.orelse[-1])

        else:
            node.orelse = [self.handle_Return(ast.Return(value=node.target))] # type: ignore
            node.end_lineno += 1 # type: ignore

        return node

    def handle_Expr(
        self,
        node: ast.Expr
    ) -> ast.Return:
        return self.handle_Return(ast.Return(value=node.value))

    def handle_Assign(
        self,
        node: ast.Assign
    ) -> ast.Return:
        if len(node.targets) > 1:
            value = ast.Tuple(elts=[ast.NamedExpr(target=target, value=node.value) for target in node.targets], ctx=ast.Load())

        else:
            value = ast.NamedExpr(target=node.targets[0], value=node.value)

        return self.handle_Return(ast.Return(value=value))

    def handle_With[With: ast.With | ast.AsyncWith](
        self,
        node: With
    ) -> With:

        node.body[-1] = self.patch_statement(node.body[-1])
        return node

    def handle_AugAssign(
        self,
        node: ast.AugAssign
    ) -> ast.Return:

        targets = [node.target]
        value = ast.copy_location(node.value, ast.BinOp(
            left = node.target,
            op = node.op,
            right = node.value
        ))

        return self.handle_Assign(ast.Assign(targets=targets, value=value))

    def handle_AnnAssign(
        self,
        node: ast.AnnAssign
    ) -> ast.Return:

        targets = [node.target]
        value = node.value

        return self.handle_Assign(ast.Assign(targets=targets, value=value))

    def handle_Try[Try: ast.Try | ast.TryStar](
        self,
        node: Try
    ) -> Try:

        if getattr(node, "finalbody", None): # If the "try" statement has a "finally" block, this block's body will contain the actual last node
            node.finalbody[-1] = self.patch_statement(node.finalbody[-1])

        elif getattr(node, "orelse", None): # If there's no "finally", "else" will be the last block
            node.orelse[-1] = self.patch_statement(node.orelse[-1])

        else: # Otherwise, we are dealing with a regular "try/except" statement
            node.body[-1] = self.patch_statement(node.body[-1])

            for handler in node.handlers:
                handler.body[-1] = self.patch_statement(handler.body[-1])

        return node

    def handle_Match(
        self,
        node: ast.Match
    ) -> ast.Match:
        for case in node.cases:
            case.body[-1] = self.patch_statement(case.body[-1])

        return node

    def handle_TypeAlias(
        self,
        node: ast.TypeAlias
    ) -> ast.Return:

        value = ast.NamedExpr(
            target=node.name,
            value=ast.Call(
                func=self.parse_expr('__import__("typing").TypeAliasType'),
                args=[
                    ast.Constant(value=node.name.id),
                    node.value
                ],
                keywords=[
                    ast.keyword(
                        arg='type_params',
                        value=ast.Tuple(
                            elts=[self.assign_type_param(param) for param in node.type_params],
                            ctx=ast.Load()
                        )
                    )
                ]
            )
        )

        return self.handle_Return(ast.Return(value=value))


    def assign_type_param(
        self,
        param: ast.type_param
    ):
        return ast.NamedExpr(
            target=ast.Name(id=param.name, ctx=ast.Store()), # type: ignore
            value=param
        )

    def change_ctx[expr: ast.expr](
        self,
        node: expr
    ) -> expr:
        node = copy(node)
        setattr(node, "ctx", ast.Load())
        return node

    def parse_expr(
        self,
        source: str
    ):
        node = ast.parse(source).body[0]
        if not isinstance(node, ast.Expr):
            raise TypeError("Given source does not evaluate a valid expression")
        return node.value

@final
class EmptyResult:
    def __init_subclass__(cls) -> NoReturn:
        raise TypeError(f"Cannot subclass {EmptyResult!r}")

@dataclass
class PatchedFrame:
    filename: str
    lineno: int
    name: str
    line: str = field(default="", init=False)
    pointer: str = field(default="", init=False)

    def __str__(self) -> str:
        frame_info = f'  File "{self.filename}", line {self.lineno}, in {self.name}'

        if self.line:
            frame_info += f"\n    {self.line}"
        if self.pointer:
            frame_info += f"\n{self.pointer}"

        return frame_info

    def __iter__(self):
        frame_info = [self.filename, self.lineno, self.name]

        if self.line:
            frame_info.append(self.line)
        if self.pointer:
            frame_info.append(self.pointer)

        return iter(frame_info)

class ExecutionInfo:
    code: str
    globals: dict[str, Any]
    locals: dict[str, Any]
    function_name: str
    result: Any
    exc_info: tuple[type[BaseException], BaseException, TracebackType]
    empty_result: bool = False

    def __init__(
        self,
        code: str,
        globals: dict[str, Any] = {},
        locals: dict[str, Any] = {},
    ) -> None:
        if not hasattr(self, "code"):
            self.code = code
        if not hasattr(self, "globals"):
            self.globals = globals
        if not hasattr(self, "locals"):
            self.locals = locals

@dataclass
class Session:
    cache:   dict[str, ExecutionInfo] = field(default_factory=lambda: {})
    globals: dict[str, Any] = field(default_factory=lambda: {})
    locals:  dict[str, Any] = field(default_factory=lambda: {})

    @property
    def variables(self) -> dict[str, Any]:
        return self.globals | self.locals

    @variables.setter
    def variables(self, value: tuple[dict[str, Any], dict[str, Any]]):
        globals, locals = value
        self.globals.update(globals)
        self.locals.update(locals)
