import typing
from .evaluator import AEvaluator


Evaluator = AEvaluator


async def aeval(
	code: str,
	glb: dict[str, typing.Any] = {},
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

		additional_vars (`Any`, *optional*):\
			Additional variables to be set on the execution context.\
			Considered as locals.

	Returns:
		Result of code evaluation or `typing.NoReturn` if the result is empty.
	"""
	return await AEvaluator().aeval(code, glb, isolate=True, **additional_vars)

eval = aeval