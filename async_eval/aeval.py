import ast
import asyncio
import sys
import typing
from copy import copy
from dataclasses import dataclass, field
from uuid import uuid4

from .utils import NodeTransformer, uniquify_name


@dataclass
class Session:
	cached_code:    dict[int, str] = field(default_factory=lambda: {})
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
		glb: dict[str, typing.Any] = None, # type: ignore
		*,
		save_vars: bool = True,
		**additional_vars: typing.Any
	) -> typing.Any:
		"""Evaluate code in asynchronous mode.

		Args:
			code (`str`):\
				The code to be evaluated.

			glb (`dict[str, Any]`, *optional*):\
				A dictionary of global variables to be set on the execution context.\
				If empty, variables from past executions will be used.

			save_vars (`bool`, *optional*):\
				If set to `True`, variables from current code execution will be saved\
				in session and can be used by future calls. Otherwise all code variables will be lost after this execution.

			additional_vars (`Any`, *optional*):\
				Additional variables to be set on the execution context.\
				Considered as locals.

		Returns:
			Result of code evaluation or `typing.NoReturn` if the result is empty
		"""

		# Use variables from past executions
		if glb is None:
			glb = self.session.globals
			additional_vars = self.session.locals

		# Store current code in the session
		code_hash = uuid4().int
		filename = f"<code {code_hash}>"
		self.session.cached_code[code_hash] = code

		if save_vars:
			glb = copy(glb)
			additional_vars = copy(additional_vars)

		# Try to execute the code and get the result
		try:
			result, variables = await self.evaluate(code, filename, glb, additional_vars)

		# Modify traceback for the occurred exception
		except Exception as e:
			traceback = self.patch_tb(*sys.exc_info())
			raise e.with_traceback(traceback)

		# Update variables if needed
		if not save_vars:
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

		typing_name = uniquify_name("typing", _globals)
		_globals[typing_name] = typing
		self.node_transformer = NodeTransformer(typing_name)

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
			name = "__amain",
			lineno = 1,
			end_lineno = module.body[-1].end_lineno,
			args = ast.arguments(posonlyargs=[], args=[], defaults=[], kwonlyargs=[]),
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
					slice = ast.Constant(value = "__amain"),
					ctx = ast.Store()
				)
			],
			value = ast.Name(id = "__amain", ctx = ast.Load()),
		)

		# Apply new code structure
		module.body = [async_main, locals_updating]
		module = ast.fix_missing_locations(module)

		# NOTE: Uncomment this line to see modified code
		# print(ast.unparse(module))

		# Compile and execute the code
		compiled = compile(module, filename=filename, mode="exec") # type: ignore
		exec(compiled, _globals, _locals)

		# Execute main code function and get the result
		result, metadata = await _locals["__amain"]()

		if not isinstance(metadata, tuple):
			self.empty_result = True
			return None, ({}, {})

		return result, metadata


# Basic usage example
async def main():
	evaluator = AEvaluator()
	result = await evaluator.aeval("code")
	evaluator.session.variables

if __name__ == "__main__":
	asyncio.run(main())