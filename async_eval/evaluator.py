import ast
import sys
import traceback
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Iterable, Optional, Union
from typing import Any, Iterable
from uuid import uuid4

from .types import (
    EmptyResult,
    ExecutionContext,
    NodeTransformer,
    PatchedFrame,
    Session
)

from .utils import (
    custom_excepthook,
    extract_pointers,
    filename_pattern
)


@dataclass
class AEvaluator:
    globals: dict[str, Any] = field(default_factory=dict)
    locals:  dict[str, Any] = field(default_factory=dict)
    session: Session = field(default_factory=Session)

    node_transformer: NodeTransformer = field(default_factory=NodeTransformer)
    last_execution:  ExecutionContext = field(default=None, kw_only=True) # type: ignore

    @property
    def empty_result(self):
        try:
            return self.last_execution.empty_result
        except AttributeError:
            raise ValueError("This evaluator has not performed any execution yet")

    @property
    def variables(self) -> dict[str, Any]:
        return self.session.variables

    async def aeval(
        self,
        code: str,
        globals: Optional[dict[str, Any]] = None,
        locals:  Optional[dict[str, Any]] = None,
        *,
        isolate: bool = False,
    ) -> Union[EmptyResult, Any]:
        """Evaluate code in asynchronous mode.

        Args:
            code (`str`):
                The code to be evaluated.

            globals (`dict[str, Any]`, *optional*):
                A dictionary of global variables to be set on the execution context.
                If empty, variables from past executions will be used.
                Defaults to an empty dictionary.

            locals (`Any`, *optional*):
                Additional variables to be set on the execution context.
                Defaults to an empty dictionary.

            isolate (`bool`, *optional*):
                If set to `False`, variables from current code execution will be saved
                in session and can be used by future calls. Otherwise, current execution
                will use only it's own variables, which won't be saved in session.
                Defaults to `False`.

        Returns:
            Result of code evaluation or `~utils.EmptyResult` if the result is empty.
        """
        globals = globals or {}
        locals = locals or {}

        # Merge the variables from previous executions
        if not isolate:
            self.globals = globals = self.globals | globals
            self.globals = locals  = self.locals  | locals

        # Empty code means empty result ;)
        code = code.strip()
        if not code:
            return EmptyResult()

        # Prepare for the new execution
        execution_id = uuid4().hex
        execution_no = getattr(self.last_execution, "no", 0) + 1
        self.session.executions[execution_id] = self.last_execution = ExecutionContext(
            execution_id, execution_no,
            code, globals, locals
        )

        # Modify the code for evaluation purposes
        module = self.node_transformer.transform_module(ast.parse(code))

        # Execute modified code and patch the traceback
        try:
            result, globals, locals = await self.last_execution._evaluate(module)
        except:
            with custom_excepthook(self._exc_handler):
                raise

        # Update variables if needed
        if not isolate:
            self.globals = globals
            self.locals = locals

            self.session.globals = globals
            self.session.locals = locals

        # Return proper result
        return EmptyResult() if self.empty_result else result

    def _exc_handler(
        self,
        exc_type:  type[BaseException],
        exc_value: BaseException,
        tb: Optional[TracebackType] = None,
    ):
        # NOTE: It is currently not possible to modify the traceback object in Python.
        # Therefore, this function simply prints patched error message to `sys.stderr`

        exc_info = (exc_type, exc_value, tb)
        print(self.format_tb(*exc_info), file=sys.stderr)

    def format_tb(
        self,
        exc_type:  type[BaseException],
        exc_value: BaseException,
        tb: Optional[TracebackType] = None,
    ) -> str:
        """
        Formats the current exception traceback from the `sys.exc_info()` into a user-friendly string.

        Returns:
            `str`: A multi-line string presenting the formatted traceback information.
        """
        frames, exc_info = self.patch_tb(exc_type, exc_value, tb)

        frames.insert(0, "Traceback (most recent call last):") # type: ignore
        frames.append(exc_info) # type: ignore

        return "\n".join(map(str, frames))

    def patch_tb(
        self,
        exc_type:  type[BaseException],
        exc_value: BaseException,
        tb: Optional[TracebackType] = None,
    ) -> tuple[list[PatchedFrame], str]:
        """
        Processes a `sys.exc_info()` traceback info for customized formatting.

        This method prepares a traceback object for display by:

        - Filtering and patching individual frames:
            * Internal frames are hidden.
            * Relevant information within frames is potentially modified.
        - Formatting the exception information based on the exception type.

        Returns:
            A list of custom frames and a string representing the exception details.
        """

        # Get list of raw patched frames
        frames = self._patch_frames(tb)

        # Pop the last frame for proper SyntaxError displaying
        if (
            exc_type is SyntaxError
            # Traceback is not empty
            and frames
            # Exception raised in one of the cached executions
            and (execution := self._get_exec_info(frames[-1].filename))
            # This cached execution is the latest one
            and execution.id == self.last_execution.id
        ):
            frames.pop()

        # Format the exception info
        exc_info = self._patch_exc_info(exc_type, exc_value)

        return frames, exc_info

    def _patch_frames(
        self,
        tb: Optional[TracebackType],
        execution: Optional[ExecutionContext] = None
    ) -> list[PatchedFrame]:
        """
        Filters and patches a traceback object (`tb`) for formatting purposes.

        This function extracts frames from the traceback, hides internal frames,
        and replaces relevant information for custom formatting.

        Args:
            `tb` (`TracebackType`): The traceback object to process. Can be `None` sometimes.
            `execution` (`ExecutionContext`, *optional*): Execution that raised the exception.
            Defaults to `self.last_execution`.

        Returns:
            A list of custom frames containing patched information for formatting.
        """
        execution = execution or self.last_execution

        # Extract native frames
        frames: Iterable[traceback.FrameSummary] = traceback.extract_tb(tb)

        # Define internal frames needed to be hidden
        self.hidden_frames = {
            (__file__, "_evaluate"),
            (__file__, "aeval"),
        }

        # Extract the pointers using regex
        pointers = extract_pointers("".join(traceback.format_tb(tb)))

        # Iterate through native frames
        patched_frames: list[PatchedFrame] = []
        for frame in frames:
            filename: str = frame.filename
            lineno: Optional[int] = frame.lineno
            line:   Optional[str] = frame.line
            function_name: str = frame.name

            # Skip unnecessary frames
            if (filename, function_name) in self.hidden_frames:
                continue

            # Get optional pointer for the current frame
            pointer: str = pointers.pop((filename, lineno, filename), None) # type: ignore

            # Exception raised in one of the cached executions
            if execution := self._get_exec_info(filename):

                # NOTE: In most cases FrameSummary.lineno shouldn't be None
                # For more info check: https://github.com/python/cpython/issues/94485#issuecomment-1172538320
                if lineno is not None:
                    code = execution.code
                    line = code.splitlines()[lineno - 1]

                filename = "<code>" if execution.no == self.last_execution.no else f"<code {execution.no}>"

            # Recreate current frame with the new info
            patched_frames.append(PatchedFrame(
                filename,
                lineno,
                filename,
                line,
                pointer
            ))

        return patched_frames

    def _get_exec_info(self, filename: str) -> Optional[ExecutionContext]:
        if search := filename_pattern.search(filename):
            code_hash: str = search.groups()[0]
            return self.session.executions.get(code_hash)

    def _patch_exc_info(
        self, exc_type: type[BaseException], exc_value: BaseException
    ) -> str:
        """
        Format exception info according to the exception type.

        Note:
            In most cases the return value will match the
            `traceback.format_exception_only`, however in some cases
            additional formatting may be applied for the syntax errors.
        """

        exc_info = "".join(traceback.format_exception_only(exc_type, exc_value))

        if (
            exc_type == SyntaxError
            # Exception was raised in one of the cached executions
            and (search := filename_pattern.search(exc_info))
            # This execution was the latest one
            and search.groups()[0] == self.last_execution.id
        ):
            exc_info = exc_info.replace(self.last_execution.filename, "<code>")

        return exc_info
