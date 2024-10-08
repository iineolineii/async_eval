from typing import Any, Union

from .evaluator import AEvaluator
from .types import EmptyResult

Evaluator = AEvaluator


async def aeval(
    code: str,
    glb: dict[str, Any] = {},
    **additional_vars: Any
) -> Union[EmptyResult, Any]:
    """Evaluate code in asynchronous mode.

    Args:
    	code (`str`):\
    		The code to be evaluated.

    	glb (`dict[str, Any]`, *optional*):\
    		A dictionary of global variables to be set on the execution context.\
    		Defaults to an empty dictionary.

    	additional_vars (`Any`, *optional*):\
    		Additional variables to be set on the execution context.\
    		Considered as locals.

    Returns:
    	Result of code evaluation or `~types.EmptyResult` if the result is empty.
    """
    return await AEvaluator().aeval(code, glb, isolate=True, **additional_vars)

eval = aeval