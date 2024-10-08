import ast
import inspect
import sys
from copy import copy
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, NoReturn, Optional, TypeVar, Union, final, overload

from .utils import CustomBuiltins


AST = TypeVar("AST", bound=ast.AST)
stmt = TypeVar("stmt", bound=ast.stmt)
For = TypeVar("For", bound=Union[ast.For, ast.AsyncFor])
With = TypeVar("With", bound=Union[ast.With, ast.AsyncWith])
Try = TypeVar("Try", bound=ast.Try)
if sys.version_info >= (3, 11):
    Try = TypeVar("Try", bound=Union[ast.Try, ast.TryStar])

# TODO: If they'll add another contextual expression
# we would need to add it here too
CONTEXTUAL_EXPR = (
    ast.Attribute,
    ast.Subscript,
    ast.Starred,
    ast.Name,
    ast.List,
    ast.Tuple
)

class NodeTransformer:
    def transform_module(self, module: ast.Module) -> ast.Module:
        self.module = module

        # Evaluate the last node
        self.module.body[-1] = self.patch_statement(self.module.body[-1])

        # Add an optional exit point at the end indicating empty result
        self.module.body.append(self.exit_node(source=self.module.body[-1]))

        # Patch all returns outside of any function or class def
        self.module = self.patch_returns(self.module)

        return self.module

    def patch_returns(
        self,
        node: AST
    ) -> AST:
        if isinstance(node, ast.Return):
            node = self.handle_Return(node)
            return node

        for name, values in ast.iter_fields(node):
            if isinstance(values, list):
                values = [
                    value
                    if isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    else self.patch_returns(value)
                    for value in values
                ]

            elif isinstance(values, ast.AST):
                values = self.patch_returns(values)

            setattr(node, name, values)

        return node

    def patch_statement(
        self,
        node: stmt
    ) -> Union[stmt, ast.Raise]:
        node.end_lineno = getattr(node, "end_lineno", node.lineno)

        if isinstance(node, ast.Assign):
            node = self.handle_Assign(node)

        elif isinstance(node, ast.AugAssign):
            node = self.handle_AugAssign(node)

        if isinstance(node, ast.AnnAssign):
            node = self.handle_AnnAssign(node)

        elif isinstance(node, (ast.For, ast.AsyncFor)):
            node = self.handle_For(node)

        elif isinstance(node, ast.If):
            node = self.handle_If(node)

        elif isinstance(node, (ast.With, ast.AsyncWith)):
            node = self.handle_With(node)

        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            node = self.handle_Import(node) # type: ignore

        elif isinstance(node, ast.Expr):
            node = self.handle_Expr(node)

        elif sys.version_info >= (3, 10):
            if isinstance(node, ast.Match):
                node = self.handle_Match(node)

            elif sys.version_info >= (3, 11):
                if isinstance(node, (ast.Try, ast.TryStar)):
                    node = self.handle_Try(node)

                elif sys.version_info >= (3, 12):
                    if isinstance(node, ast.TypeAlias):
                        node = self.handle_TypeAlias(node)

        return node

    def handle_Return(
        self,
        node: ast.Return
    ) -> ast.Raise:
        return self.exit_node(result=node.value, source=node)

    def handle_Assign(
        self,
        node: ast.Assign
    ):
        # Move original node one line upper
        self.module.body.insert(node.lineno-1, node)

        # And return all or one single of it's targets
        if len(node.targets) == 1:
            result = node.targets[0]
        else:
            result = ast.Tuple(elts=node.targets)

        return self.exit_node(result=result)

    def handle_AugAssign(
        self,
        node: ast.AugAssign
    ):
        targets = [node.target]
        value = ast.copy_location(node.value, ast.BinOp(
            left = node.target,
            op = node.op,
            right = node.value
        ))

        return self.handle_Assign(ast.Assign(targets=targets, value=value)) # type: ignore

    def handle_AnnAssign(
        self,
        node: ast.AnnAssign
    ):
        targets = [node.target]
        value = node.value

        return self.handle_Assign(ast.Assign(targets=targets, value=value)) # type: ignore

    def handle_For(
        self,
        node: For
    ) -> For:
        if node.orelse:
            node.orelse[-1] = self.patch_statement(node.orelse[-1])

        else:
            node.orelse = [self.exit_node(value=node.target)] # type: ignore
            node.end_lineno += 1 # type: ignore

        return node

    def handle_If(
        self,
        node: ast.If
    ) -> ast.If:
        node.body[-1] = self.patch_statement(node.body[-1])

        if getattr(node, "orelse", None):
            node.orelse[-1] = self.patch_statement(node.orelse[-1])

        return node

    def handle_With(
        self,
        node: With
    ) -> With:

        node.body[-1] = self.patch_statement(node.body[-1])
        return node

    def handle_Try(
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

    def handle_Import(
        self,
        node: Union[ast.Import, ast.ImportFrom]
    ):
        names = [
            ast.Name(name.asname or name.name)
            for name in node.names
        ]
        # Move original node one line upper
        self.module.body.insert(node.lineno-1, node)

        # And return all or one single of it's targets
        value = names[0] if len(names) == 1 else ast.Tuple(elts=names) # type: ignore
        return self.exit_node(result=value)

    def handle_Expr(
        self,
        node: ast.Expr
    ):
        value = node.value
        return self.exit_node(result=value)

    if sys.version_info >= (3, 10):
        def handle_Match(
            self,
            node: Match
        ) -> Match:
            for case in node.cases:
                case.body[-1] = self.patch_statement(case.body[-1])

            return node

        if sys.version_info >= (3, 12):
            def handle_TypeAlias(
                self,
                node: ast.TypeAlias
            ):
                return self.handle_Assign(ast.Assign([node.name], node.value))

            def assign_type_param(
                self,
                param: ast.type_param
            ):
                return ast.NamedExpr(
                    target=ast.Name(id=param.name, ctx=ast.Store()), # type: ignore
                    value=param # type: ignore
                )


    # Utilities
    @overload
    def exit_node(
        self,
        *,
        result: ast.expr,
        source: None = None
    ) -> ast.Raise: ...
    @overload
    def exit_node(
        self,
        *,
        result: None = None,
        source: ast.AST
    ) -> ast.Raise: ...
    @overload
    def exit_node(
        self,
        *,
        result: ast.expr,
        source: ast.AST
    ) -> ast.Raise: ...
    def exit_node(
        self,
        *,
        result: Optional[ast.expr] = None,
        source: Optional[ast.AST] = None # type: ignore
    ) -> ast.Raise:
        """
        Allows to get the result of code evaluation and carry its globals and locals
        by generating an exit point which is a custom `EvaluatorExit` exception call.
        The given `result` along with `globals()` and `locals()` calls are passed as arguments to `EvaluatorExit`.

        Parameters:
        - result (ast.expr): The patched expression representing the value to be returned.
        - source (ast.AST) : The AST node representing the original node where the result is computed.

        Returns:
        - ast.Raise: An AST node representing the `EvaluatorExit` call.
        """
        if result is None and source is None:
            raise ValueError("Both result_node and source_node cannot be None")

        result = copy(result)
        source = copy(source) # type: ignore

        args = [] if result is None else [result]
        source: ast.AST = source or result # type: ignore

        # Forcefully apply the Load context to the result node and all its elts
        if (
            isinstance(result, CONTEXTUAL_EXPR) # If node should have a context
            or hasattr(result, "ctx")           # Or it already has one
        ):
            if hasattr(result, "elts"):
                result.elts = list(*result.elts) # type: ignore

                # TODO: If they'll add another contextual expression
                # with the child container other than "elts",
                # we would need to implement support for that one too like this:
                # for elt in result.elts + ANOTHER_CHILD_CONTAINER:
                for elt in result.elts: # type: ignore
                    elt = copy(elt)
                    ctx = getattr(elt, "ctx", ast.Load())

                    if not isinstance(ctx, ast.Load):
                        elt.ctx = ast.Load()

            setattr(result, "ctx", ast.Load())

        globals = self.parse_expr("globals()")
        locals  = self.parse_expr( "locals()")

        # Create an exit point
        patched = ast.Raise(
            exc=ast.Call(
                func=ast.Name(
                    id="EvaluatorExit",
                    ctx=ast.Load()
                ),
                args=[globals, locals] + args,
                keywords=[]
            )
        )
        patched = ast.copy_location(patched, source)
        return ast.fix_missing_locations(patched)

    def parse_expr(
        self,
        source: str
    ):
        node = ast.parse(source).body[0]
        if not isinstance(node, ast.Expr):
            raise TypeError("Given source does not evaluate a valid expression")
        return node.value


@dataclass
class ExecutionContext:
    id: str
    """
    id (`str`):
        Unique hex ID of the execution.
        Not shared between different evaluators.
    """
    no: int
    """
    no (`int`):
        Sequential number of the execution, mainly used for display purposes.
        May overlap between different evaluators.
    """
    code: str
    """
    code (`str`):
        Original source code of the execution.
    """
    globals: dict[str, Any] = field(default_factory=dict)
    """
    globals (`dict[str, Any]`, *optional*):
        Global variables of the execution.
        Defaults to an empty dictionary.
    """
    locals:  dict[str, Any] = field(default_factory=dict)
    """
    locals (`dict[str, Any]`, *optional*):
        Local variables of the execution.
        Defaults to an empty dictionary.
    """
    exc_info: tuple[type[BaseException], BaseException, Optional[TracebackType]] = field(init=False)
    """
    exc_info (`tuple[type[BaseException], BaseException, TracebackType | None]`, *optional*):
        Exception information of the execution.
        Defaults to `None`.
    """
    empty_result: bool = field(default=False, init=False)
    """
    empty_result (`bool`, *optional*):
        A flag indicating whether the execution resulted in an empty result.
        NOTE: In theory, this flag can be `True` only if the execution code is empty
        or ends with a statement that is not supported by the used node transformer.
    """
    result: Any = field(init=False)

    def __post_init__(self) -> None:
        self.filename = f"<aeval {self.no} {self.id}>"

    async def _evaluate(
        self, module: ast.Module
    ) -> tuple[Any | None, dict[str, Any], dict[str, Any]]:
        """
        Executes the patched code and returns the result of this execution along with it's globals and locals.

        Parameters:
        - module (ast.Module): The patched code to be executed.

        Returns:
        - A tuple containing three items:
            1. result  (Any | None)     - The result of code evaluation or `None` if result is empty
            2. globals (dict[str, Any]) - The global variables of this execution
            3. locals  (dict[str, Any]) - The local  variables of this execution

        Raises:
        - Exception: If an exception occurs during the execution of the code.
        """
        # Let's leave it here for debugging purposes
        self._original_code = ast.unparse(module)

        # Determinate whether the code is asynchronous or not
        flags = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        compiled_code = compile(module, self.filename, "exec", flags)
        is_async = inspect.CO_COROUTINE & compiled_code.co_flags == inspect.CO_COROUTINE

        # Execute the code with the isolated builtins
        with CustomBuiltins({"EvaluatorExit": EvaluatorExit}):
            try:
                if is_async:
                    await eval(compiled_code, self.globals, self.locals)
                else:
                    exec(compiled_code, self.globals, self.locals)

            # Catch an exit signal and carry the result info
            except EvaluatorExit as result_info:
                self.globals = result_info.globals
                self.locals  = result_info.locals
                self.result  = result_info.result

                self.empty_result = result_info.empty_result

            except Exception:
                self.exc_info = sys.exc_info() # type: ignore
                raise

        return self.result, self.globals, self.locals

@dataclass
class Session:
    executions: dict[str, ExecutionContext] = field(default_factory=dict)
    globals: dict[str, Any] = field(default_factory=dict)
    locals:  dict[str, Any] = field(default_factory=dict)

    @property
    def variables(self) -> dict[str, Any]:
        return self.globals | self.locals

    @variables.setter
    def variables(self, value: tuple[dict[str, Any], dict[str, Any]]):
        globals, locals = value
        self.globals.update(globals)
        self.locals.update(locals)


@final
class EmptyResult:
    def __init_subclass__(cls) -> NoReturn:
        raise TypeError(f"Cannot subclass {EmptyResult!r}")

@dataclass
class PatchedFrame:
    filename: str
    lineno: Optional[int]
    name: str
    line: Optional[str] = field(default=None)
    pointer: Optional[str] = field(default=None)

    def __str__(self) -> str:

        if self.lineno:
            frame_info = f'  File "{self.filename}", line {self.lineno}, in {self.name}'
        else:
            frame_info = f'  File "{self.filename}", in {self.name}'

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

class EvaluatorExit(Exception):
    def __init__(
        self,
        globals: dict[str, Any],
        locals:  dict[str, Any],
        *result
    ) -> None:
        self.globals = globals
        self.locals = locals

        # If tuple is empty then result is empty too
        if result:
            self.empty_result = False
            self.result = result[0]
        else:
            self.empty_result = True
            self.result = None

        return super().__init__()
