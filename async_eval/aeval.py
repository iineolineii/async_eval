import ast
import asyncio
import re
import sys
import traceback
import typing
from dataclasses import dataclass, field
from types import TracebackType
from uuid import uuid4

from .utils import NodeTransformer, extract_pointers, uniquify_name


uuid4match = r"[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}"
code_filename = re.compile(rf"\<({uuid4match})\>")

@dataclass
class Session:
	cached_code:    dict[str, str] = field(default_factory=lambda: {})
	globals: dict[str, typing.Any] = field(default_factory=lambda: {})
	locals:  dict[str, typing.Any] = field(default_factory=lambda: {})

	@property
	def variables(self) -> dict[str, typing.Any]:
		return self.globals | self.locals

	@variables.setter
	def variables(self, value: tuple[dict[str, typing.Any], dict[str, typing.Any]]):
		globals, locals = value
		self.globals.update(globals)
		self.locals.update(locals)


class AEvaluator:
	def __init__(self) -> None:
		self.session = Session()
		self.empty_result = False

	async def aeval(
		self,
		code: str,
		glb: dict[str, typing.Any] = {},
		*,
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
			additional_vars.update(self.session.variables)

		# Store current code in the session
		code_hash = str(uuid4())
		filename = f"<code {code_hash}>"
		self.session.cached_code[code_hash] = code

		# Try to execute user's code
		try:
			result, variables = await self.evaluate(
				code,
				filename,
				glb,
				additional_vars
			)
		# Patch raised exception
		except:
			self.exc_handler(*sys.exc_info()) # type: ignore

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
		function_name = uniquify_name("amain", _globals)

		# Empty code means empty result ;)
		if not code:
			self.empty_result = True
			return None, ({}, {})

		# Parse the code
		module = ast.parse(code, filename="<code>")

		# Modify the code for evaluation
		module: ast.Module = self.node_transformer.transform_module(module)

		# Wrap the code in async function
		async_main = ast.AsyncFunctionDef(
			name = function_name,
			lineno = 1,
			end_lineno = module.body[-1].end_lineno,
			args = ast.arguments(posonlyargs=[], args=[], defaults=[], kwonlyargs=_locals.keys()),
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
					slice = ast.Constant(value = function_name),
					ctx = ast.Store()
				)
			],
			value = ast.Name(id = function_name, ctx = ast.Load()),
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
		result, metadata = await _locals[function_name](**_locals)

		if not isinstance(metadata, tuple):
			self.empty_result = True
			return None, ({}, {})

		return result, metadata

	def exc_handler(
		self,
		exc_type: type[BaseException],
		exc_value: BaseException,
		tb: TracebackType = None, # type: ignore
	):
		# TODO: Make some tests and determinate which frames needs to be hidden from the final traceback
		hidden_frames = {}
		pointers = extract_pointers("".join(traceback.format_tb(tb)))
		frames: list[traceback.FrameSummary] = traceback.extract_tb(tb)
		tb_lines: list[str] = []

		for frame in frames:
			filename: str = frame.filename
			lineno: int | None = frame.lineno
			name: str = frame.name
			line: str | None = frame.line

			# Skip unnecessary frames
			if (filename, name) in hidden_frames:
				continue

			# Exception was raised in one of the cached codes
			if search := code_filename.search(filename):
				filename = "<code>"
				code_hash = search.groups()[0]

				# NOTE: In most cases FrameSummary.lineno shouldn't be None
				if lineno is not None:
					line = self.session.cached_code.get(code_hash)

			pointer: str | None = pointers.pop((filename, lineno, name), None) # type: ignore

			# Format current frame
			tb_lines.append(f'  File "{filename}", line {lineno}, in {name}')
			if line:
				tb_lines.append(f'    {line}')
			if pointer:
				tb_lines.append(pointer)

		print("\n".join(tb_lines))


# Basic usage example
async def main():
	evaluator = AEvaluator()
	result = await evaluator.aeval("code")
	print(evaluator.session.variables)

if __name__ == "__main__":
	asyncio.run(main())