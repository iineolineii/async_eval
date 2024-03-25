import ast
import sys
import traceback
import typing
from types import TracebackType
from uuid import uuid4

from .utils import (ExecutionInfo, NodeTransformer, Session, PatchedFrame,
                    extract_pointers, filename_pattern, uniquify_name)


class AEvaluator:
	def __init__(
		self,
		session: Session = None, # type: ignore
		node_transformer: NodeTransformer = None # type: ignore
	) -> None:

		self.session = Session() if session is None else session
		self.node_transformer = NodeTransformer() if node_transformer is None else node_transformer
		self.empty_result = False

	@property
	def variables(self) -> dict[str, typing.Any]:
		return self.session.variables

	async def aeval(
		self,
		code: str,
		glb: dict[str, typing.Any] = {},
		isolate: bool = False,
		**additional_vars: typing.Any
	) -> typing.Any:
		"""Evaluate code in asynchronous mode.

		Args:
			code (`str`):
				The code to be evaluated.

			glb (`dict[str, Any]`, *optional*):
				A dictionary of global variables to be set on the execution context.\
				If empty, variables from past executions will be used.\
				Defaults to an empty dictionary.

			isolate (`bool`, *optional*):
				If set to `False`, variables from current code execution will be saved\
				in session and can be used by future calls. Otherwise, current execution\
				will use only it's own variables, which won't be saved in session.\
				Defaults to False.

			additional_vars (`Any`, *optional*):
				Additional variables to be set on the execution context.\
				Considered as locals.

		Returns:
			Result of code evaluation or `typing.NoReturn` if the result is empty.
		"""

		# Make shallow copies of variable scopes
		glb = glb.copy() or {}
		additional_vars = additional_vars.copy()

		# Use variables from past executions
		# and pre-save current ones in the session
		if not isolate:
			glb.update(self.session.globals)
			additional_vars.update(self.session.locals)

			# Save execution info
			exec_info = ExecutionInfo(code, glb, additional_vars)
			self.session.cache[self.code_hash] = exec_info

		# Store current code in the session
		self.code_hash = str(uuid4())
		filename = f"<code {self.code_hash}>"

		# Setup excepthook for executing the code
		# and bring back the old one after this
		excepthook = sys.excepthook
		sys.excepthook = self._exc_handler
		result, variables = await self._evaluate(
			code,
			filename,
			glb,
			additional_vars
		)
		sys.excepthook = excepthook

		# Update variables if needed
		if not isolate:
			self.session.variables = variables
			exec_info.globals, exec_info.locals = variables

		# Return proper result
		return result if not self.empty_result else typing.NoReturn

	async def _evaluate(
		self,
		code: str,
		filename: str,
		_globals: dict[str, typing.Any],
		_locals: dict[str, typing.Any]
	) -> tuple[typing.Any, tuple[dict[str, typing.Any], dict[str, typing.Any]]]:
		code = code.strip()

		# Make sure that typing is not overridden anywhere in the locals
		typing_name = uniquify_name(self.node_transformer.typing_name, _locals)
		_locals[typing_name] = typing
		self.node_transformer.typing_name = typing_name

		# Make sure that main function name is not overridden anywhere in the globals
		self.function_name = uniquify_name("amain", _globals)

		# Empty code means empty result ;)
		if not code:
			self.empty_result = True
			return None, ({}, {})

		# Parse the code
		module = ast.parse(code, filename=filename)

		# Modify the code for evaluation
		module: ast.Module = self.node_transformer.transform_module(module)

		# Create arguments list from the locals
		kwonlyargs = [ast.arg(arg=name) for name in _locals]
		kw_defaults = [None] * len(kwonlyargs)

		# Wrap the code in async function
		async_main = ast.AsyncFunctionDef(
			name = self.function_name,
			lineno = 1,
			end_lineno = module.body[-1].end_lineno,
			args = ast.arguments(posonlyargs=[], args=[], defaults=[], kwonlyargs=kwonlyargs, kw_defaults=kw_defaults),
			body = module.body,
			decorator_list = [],
			returns = None
		)

		# Add main function to locals
		locals_updating = ast.Assign(
			lineno = module.body[-1].end_lineno,
			end_lineno = module.body[-1].end_lineno,
			targets = [
				ast.Subscript(
					value = ast.Call(
						func = ast.Name(id = "locals", ctx = ast.Load()),
						args = [],
						keywords = [],
						lineno = 1,
						col_offset = 0
					),
					slice = ast.Constant(value = self.function_name),
					ctx = ast.Store()
				)
			],
			value = ast.Name(id = self.function_name, ctx = ast.Load()),
		)

		# Apply new code structure
		module.body = [async_main, locals_updating]
		module = ast.fix_missing_locations(module)

		# NOTE: Uncomment this line to see modified code
		# print(ast.unparse(module))

		# Compile and execute the code
		compiled = compile(module, filename=filename, mode="exec") # type: ignore
		exec(compiled, _globals)

		# Execute main code function and get the result
		result = await _globals[self.function_name](**_locals)

		# There's no value for result
		if len(result) == 2:
			self.empty_result = True
			glb, loc = result
			result = [None, glb, loc]

		result, *variables = result
		return result, variables # type: ignore

	def _exc_handler(
		self,
		exc_type: type[BaseException],
		exc_value: BaseException,
		tb: TracebackType = None, # type: ignore
	):
		print(self.format_tb(exc_type, exc_value, tb))

	def format_tb(
		self,
		exc_type: type[BaseException],
		exc_value: BaseException,
		tb: TracebackType = None, # type: ignore
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
		exc_type: type[BaseException],
		exc_value: BaseException,
		tb: TracebackType = None, # type: ignore
	) -> tuple[list[PatchedFrame], str]:
		"""
		Processes a `sys.exc_info()` traceback info for customized formatting.

		This method prepares a traceback object for display by:

		- Filtering and patching individual frames:
			- Internal frames are hidden.
			- Relevant information within frames is potentially modified.
		- Formatting the exception information based on the exception type.

		Returns:
			A list of custom frames and a string representing the exception details.
		"""

		# Get list of raw patched frames
		frames = self._patch_frames(tb)

		# Pop the last frame for proper SyntaxError displaying
		if (
			exc_type == SyntaxError
			and frames # Traceback is not empty
			and (exec_info := self._get_exec_info(frames[-1].filename)) # Exception raised in one of the cached executions
			and exec_info.function_name == self.function_name # Exception originating from the current execution code
		):
			frames.pop()

		# Format the exception info
		exc_info = self._patch_exc_info(exc_type, exc_value)

		return frames, exc_info

	def _patch_frames(
		self,
		tb: TracebackType = None, # type: ignore
	) -> list[PatchedFrame]:
		"""
		Filters and patches a traceback object (`tb`) for formatting purposes.

		This function extracts frames from the traceback, hides internal frames,
		and replaces relevant information for custom formatting.

		Args:
			`tb` (`TracebackType`): The traceback object to process.

		Returns:
			A list of custom frames containing patched information for formatting.
		"""

		# Extract native frames
		frames: typing.Iterable[traceback.FrameSummary] = traceback.extract_tb(tb)

		# Define internal frames needed to be hidden
		self.hidden_frames = {(__file__, self.function_name), (__file__, self._evaluate.__name__), (__file__, self.aeval.__name__)}

		# Extract the pointers using regex
		pointers = extract_pointers("".join(traceback.format_tb(tb)))

		# Iterate through native frames
		patched_frames: list[PatchedFrame] = []
		for frame in frames:
			filename: str = frame.filename
			lineno: int | None = frame.lineno
			name: str = frame.name
			line: str | None = frame.line

			# Skip unnecessary frames
			if (filename, name) in self.hidden_frames:
				continue

			# Exception raised in one of the cached executions
			if exec_info := self._get_exec_info(filename):

				# Exception originating from the current execution code
				if name == exec_info.code:
					name = "<module>"

				# NOTE: In most cases FrameSummary.lineno shouldn't be None
				# For more info check: https://github.com/python/cpython/issues/94485#issuecomment-1172538320
				if lineno is not None:
					code = exec_info.code
					line = code.splitlines()[lineno-1]

				filename = "<code>"

			# Get optional pointer for the current frame
			pointer: str = pointers.pop((filename, lineno, name), "") # type: ignore

			# Recreate current frame with new info
			patched_frames.append(PatchedFrame(filename, lineno, name)) # type: ignore

			# Adjust frame info if possible
			if line:
				patched_frames[-1].line = line
			if pointer:
				patched_frames[-1].pointer = pointer

		return patched_frames

	def _get_exec_info(
		self,
		filename: str
	) -> typing.Union[ExecutionInfo, None]:

		if search := filename_pattern.search(filename):
			code_hash: str = search.groups()[0]
			exec_info = self.session.cache.get(code_hash)

		return exec_info

	def _patch_exc_info(
		self,
		exc_type: type[BaseException],
		exc_value: BaseException
	) -> str:
		"""
		Format exception info according to the exception type.

		Note:
			In most cases the return value will match the
			`traceback.format_exception_only`, however in some cases
			additional formatting may be applied for the syntax errors.
		"""

		exc_info = "".join(traceback.format_exception_only(exc_type, exc_value))

		if exc_type == SyntaxError:
			if search := filename_pattern.search(exc_info):
				code_hash = search.groups()[0]

				if code_hash == self.code_hash:
					exc_info = exc_info.replace(f'<code {code_hash}>', "<code>")

		return exc_info
