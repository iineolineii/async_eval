import ast
import re
import sys
import traceback
import typing
from types import TracebackType
from uuid import uuid4

from .utils import (NodeTransformer, Session, extract_pointers,
                    filename_pattern, uniquify_name)


class AEvaluator:
	def __init__(self) -> None:
		self.session = Session()
		self.empty_result = False

	async def aeval(
		self,
		code: str,
		glb: dict[str, typing.Any] = {},
		isolate: bool = False,
		**additional_vars: typing.Any
	) -> typing.Any:
		"""Evaluate code in asynchronous mode.

		Args:
			code (`str`):\
				The code to be evaluated.

			glb (`dict[str, Any]`, *optional*):\
				A dictionary of global variables to be set on the execution context.\
				If empty, variables from past executions will be used.\
				Defaults to an empty dictionary.

			isolate (`bool`, *optional*):\
				If set to `False`, variables from current code execution will be saved\
				in session and can be used by future calls. Otherwise, current execution\
				will use only it's own variables, which won't be saved in session.\
				Defaults to False.

			additional_vars (`Any`, *optional*):\
				Additional variables to be set on the execution context.\
				Considered as locals.

		Returns:
			Result of code evaluation or `typing.NoReturn` if the result is empty.
		"""

		# Make shallow copies of variable scopes
		glb = glb.copy() or {}
		additional_vars = additional_vars.copy()

		# Use variables from past executions
		if not isolate:
			glb.update(self.session.globals)
			additional_vars.update(self.session.locals)

		# Store current code in the session
		self.code_hash = str(uuid4())
		filename = f"<code {self.code_hash}>"
		self.session.cached_code[self.code_hash] = code

		excepthook = sys.excepthook
		sys.excepthook = self.exc_handler
		result, variables = await self.evaluate(
			code,
			filename,
			glb,
			additional_vars
		)
		sys.excepthook = excepthook

		# Update variables if needed
		if not isolate:
			self.session.variables = variables

		# Return proper result
		return result if not self.empty_result else typing.NoReturn

	async def evaluate(
		self,
		code: str,
		filename: str,
		_globals: dict[str, typing.Any],
		_locals: dict[str, typing.Any]
	) -> tuple[typing.Any, tuple[dict[str, typing.Any], dict[str, typing.Any]]]:
		code = code.strip()

		# Make sure that typing is not overridden anywhere in the locals
		typing_name = uniquify_name("typing", _locals)
		_locals[typing_name] = typing
		self.node_transformer = NodeTransformer(typing_name)

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

	def exc_handler(
		self,
		exc_type: type[BaseException],
		exc_value: BaseException,
		tb: TracebackType = None, # type: ignore
	):
		frames = self.format_frames(tb)
		frames.insert(0, "Traceback (most recent call last):")
		exc_info = self.format_exc_info(exc_value)

		if exc_type == SyntaxError:
			if search := filename_pattern.search(exc_info):
				code_hash = search.groups()[0]

				if code_hash == self.code_hash:
					exc_info = exc_info.replace(f'<code {code_hash}>', "<code>")
					del frames[-1]

		frames.append(exc_info)
		print("\n".join(frames))

	def format_frames(
		self,
		tb: TracebackType
	) -> list[str]:
		frames: typing.Iterable[traceback.FrameSummary] = traceback.extract_tb(tb)

		hidden_frames = {(__file__, self.function_name), (__file__, "evaluate"), (__file__, "aeval")}
		pointers = extract_pointers("".join(traceback.format_tb(tb)))

		patched_frames = []
		for frame in frames:
			filename: str = frame.filename
			lineno: int | None = frame.lineno
			name: str = frame.name
			line: str | None = frame.line

			# Skip unnecessary frames
			if (filename, name) in hidden_frames:
				continue

			# Exception was raised in one of the cached codes
			if search := filename_pattern.search(filename):
				code_hash = search.groups()[0]

				if code_hash not in self.session.cached_code:
					continue

				filename = "<code>"

				if name == self.function_name:
					name = "<module>"

				# NOTE: In most cases FrameSummary.lineno shouldn't be None
				if lineno is not None:
					code = self.session.cached_code[code_hash]
					line = code.splitlines()[lineno-1]

			pointer: str | None = pointers.pop((filename, lineno, name), None) # type: ignore

			# Format current frame
			patched_frames.append(f'  File "{filename}", line {lineno}, in {name}')
			if line:
				patched_frames[-1] += f"\n    {line}"
			if pointer:
				patched_frames[-1] += f"\n{pointer}"
			patched_frames[-1] += "\n"

		return patched_frames

	def format_exc_info(
		self,
		exc_value: BaseException
	):
		# Format exception info
		exc_info = "".join(traceback.format_exception_only(exc_value))
		return exc_info
