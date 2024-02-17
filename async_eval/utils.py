from typing import Iterable


def uniquify_name(name: str, namespace: Iterable[str]) -> str:
	"""Generate completely unique name based on the old name

	Args:
		name (str): The old name that needs to be changed
		namespace (Iterable[str]): Names that need to be checked for uniqueness

	Returns:
		str: The new unique name

	Examples:
		.. code-block:: python
		>>> uniquify_name("foo", {"foo", "bar", "baz"})
		'_foo'
		>>> uniquify_name("bar", {"foo", "bar", "baz"})
		'_bar'
	"""
	while name in namespace:
		name = "_" + name

	return name
