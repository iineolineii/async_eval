import asyncio
from dataclasses import dataclass, field
import sys
import typing
from uuid import uuid4

@dataclass
class Session:
	cached_code: dict[int, str] = field(default_factory=lambda: {})
	globals: dict[str, typing.Any] = field(default_factory=lambda: {})
	locals: dict[str, typing.Any] = field(default_factory=lambda: {})

	@property
	def variables(self) -> dict[str, typing.Any]:
		return self.globals | self.locals


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
		code_hash = uuid4().int
		filename = f"<code> {code_hash}"
		self.session.cached_code[code_hash] = code

		try:
			result, variables = await self.evaluate(code_hash, filename, glb, additional_vars, save_vars)

		except Exception as e:
			traceback = self.patch_tb(*sys.exc_info())
			raise e.with_traceback(traceback)

		self.session.variables.update(variables)
		return result


async def main():
	evaluator = AEvaluator()
	result = await evaluator.aeval("code")
	evaluator.variables

if __name__ == "__main__":
	asyncio.run(main())