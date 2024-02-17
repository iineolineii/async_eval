import asyncio
from dataclasses import dataclass, field
import sys
import typing
from uuid import uuid4

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

	async def aeval(
		self,
		code: str,
		glb: dict[str, typing.Any] = {},
		*,
		save_vars: bool = True,
		**additional_vars: typing.Any
	) -> typing.Any:
		"""Evaluate code in asynchronous mode.

		Args:
			code (`str`): The code to be evaluated.
			glb (`dict[str, Any]`, *optional*): A dictionary of global variables to be set on the execution context. Defaults to an empty dict.
			save_vars (`bool`, *optional*): If set to `True`, variables from current code execution will be saved session and can be used by future calls. Otherwise all code variables will be lost after it's execution.
			additional_vars (`Any`, *optional*): Additional variables to be set on the execution context. Will be merged with `glb` after code execution, considered as locals.

		Returns:
			Result of code evaluation or `typing.NoReturn` if the result is empty
		"""
		glb = glb or {} # Explicitly create a new dictionary for empty globals

		# Store current code in the session
		code_hash = uuid4().int
		filename = f"<code> {code_hash}"
		self.session.cached_code[code_hash] = code

		# Try to execute the code and get the result
		try:
			result, variables = await self.evaluate(code, filename, glb, additional_vars)

		# Modify traceback for the occurred exception
		except Exception as e:
			traceback = self.patch_tb(*sys.exc_info())
			raise e.with_traceback(traceback)

		# Update variables if needed
		if save_vars:
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
		...


# Basic usage example
async def main():
	evaluator = AEvaluator()
	result = await evaluator.aeval("code")
	evaluator.session.variables

if __name__ == "__main__":
	asyncio.run(main())