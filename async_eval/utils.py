import ast
from copy import copy
import re
from typing import Iterable


def uniquify_name(name: str, namespace: Iterable[str]) -> str:
	"""Generate completely unique name based on the old name

	Args:
		name (str): The old name that needs to be changed
		namespace (Iterable[str]): Names that need to be checked for uniqueness

	Returns:
		str: The new unique name

	Examples:
		>>> uniquify_name("foo", {"foo", "bar", "baz"})
		'_foo'
		>>> uniquify_name("bar", {"foo", "bar", "baz"})
		'_bar'
	"""
	while name in namespace:
		name = "_" + name

	return name

def extract_pointers(traceback_text: str) -> dict[tuple[str, int, str], str]:
	pattern = r'\s+File "(.*?)", line (\d+), in (.*?)\n\s+.*?\n(\s+[~^]+)'
	matches: list[str] = re.findall(pattern, traceback_text)

	pointers = {
		(filename, int(lineno), name): pointer
		for filename, lineno, name, pointer in matches
	}

	return pointers

class NodeTransformer:
	def __init__(
		self,
		typing_name: str = "typing"
	) -> None:
		self.typing_name = typing_name

	def transform_module(
		self,
		module: ast.Module
	) -> ast.Module:

		module = self.patch_returns(module) # Patch all returns outside of any function def
		module.body[-1] = self.patch_statement(module.body[-1])

		# Empty result
		value = ast.Constant(
			value = True,
			kind = None
		)

		node = self.handle_Return(ast.Return(value=value))
		node.lineno = node.end_lineno = module.body[-1].end_lineno # type: ignore
		module.body.append(node)

		return module

	def patch_returns[AST: ast.AST](
		self,
		node: AST
	) -> AST:

		if isinstance(node, ast.Return):
			node = self.handle_Return(node)
			return node

		for name, values in ast.iter_fields(node):

			if isinstance(values, list):
				values = [
					self.patch_returns(value)
					if not isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef))
					else value
					for value in values
				]

			elif isinstance(values, ast.AST):
				values = self.patch_returns(values)

			setattr(node, name, values)

		return node

	def patch_statement[stmt: ast.stmt](
		self,
		node: stmt
	) -> stmt | ast.Return:

		old_node = node
		node.end_lineno = self.get_end_lineno(node)

		if isinstance(node, ast.If):
			node = self.handle_If(node)

		elif isinstance(node, (ast.For, ast.AsyncFor)):
			node = self.handle_For(node)

		elif isinstance(node, ast.Assign):
			node = self.handle_Assign(node)

		elif isinstance(node, ast.AugAssign):
			node = self.handle_AugAssign(node)

		elif isinstance(node, (ast.With, ast.AsyncWith)):
			node = self.handle_With(node)

		elif isinstance(node, ast.Expr):
			node = self.handle_Expr(node)

		elif isinstance(node, (ast.Try, ast.TryStar)):
			node = self.handle_Try(node)

		elif isinstance(node, ast.Match):
			node = self.handle_Match(node)

		elif isinstance(node, ast.TypeAlias):
			node = self.handle_TypeAlias(node)

		node = ast.copy_location(node, old_node)
		return node


	def handle_Return(
		self,
		node: ast.Return
	) -> ast.Return:
		"""Patch a single return. Add globals and locals call to return"s value"""
		value = copy(node.value)

		# NOTE: In theory, this can only happen with for's target node, but who knows...
		if hasattr(value, "ctx") and not isinstance(getattr(node.value, "ctx"), ast.Load):
			# Multi-target loop
			if isinstance(value, ast.Tuple):
				value.elts = [self.change_ctx(elt) for elt in value.elts]

			setattr(value, "ctx", ast.Load())

		glb = ast.copy_location(ast.Call(func=ast.Name(id = "globals", ctx = ast.Load()), args=[], keywords=[]), node)
		loc = ast.copy_location(ast.Call(func=ast.Name(id = "locals", ctx = ast.Load()), args=[], keywords=[]), node)

		update = ast.copy_location(ast.BinOp(
			left=glb,
			op=ast.BitOr(),
			right=loc
		), node)

		value = ast.Tuple(elts=[value, update], ctx=ast.Load())
		value = ast.copy_location(value, node)

		patched = ast.Return(value=value)
		patched = ast.copy_location(patched, node)

		return patched

	def handle_If(
		self,
		node: ast.If
	) -> ast.If:
		node.body[-1] = self.patch_statement(node.body[-1])

		if getattr(node, "orelse", None):
			node.orelse[-1] = self.patch_statement(node.orelse[-1])

		return node

	def handle_For[For: ast.For | ast.AsyncFor](
		self,
		node: For
	) -> For:
		if getattr(node, "orelse", None):
			node.orelse[-1] = self.patch_statement(node.orelse[-1])

		else:
			node.orelse = [self.handle_Return(ast.Return(value=node.target))] # type: ignore
			node.end_lineno += 1 # type: ignore

		return node

	def handle_Expr(
		self,
		node: ast.Expr
	) -> ast.Return:
		return self.handle_Return(ast.Return(value=node.value))

	def handle_Assign(
		self,
		node: ast.Assign
	) -> ast.Return:
		if len(node.targets) > 1:
			value = ast.Tuple(elts=[ast.NamedExpr(target=target, value=node.value) for target in node.targets], ctx=ast.Load())

		else:
			value = ast.NamedExpr(target=node.targets[0], value=node.value)

		return self.handle_Return(ast.Return(value=value))

	def handle_With[With: ast.With | ast.AsyncWith](
		self,
		node: With
	) -> With:

		node.body[-1] = self.patch_statement(node.body[-1])
		return node

	def handle_AugAssign(
		self,
		node: ast.AugAssign
	) -> ast.Return:

		targets = [node.target]
		value = ast.copy_location(node.value, ast.BinOp(
			left = node.target,
			op = node.op,
			right = node.value
		))

		return self.handle_Assign(ast.Assign(targets=targets, value=value))

	def handle_AnnAssign(
		self,
		node: ast.AnnAssign
	) -> ast.Return:

		targets = [node.target]
		value = node.value

		return self.handle_Assign(ast.Assign(targets=targets, value=value))

	def handle_Try[Try: ast.Try | ast.TryStar](
		self,
		node: Try
	) -> Try:

		if getattr(node, "finalbody", None): # If the "try" statement has a "finally" block, this block's body will contain the actual last node
			node.finalbody[-1] = self.patch_statement(node.finalbody[-1])

		elif getattr(node, "orelse", None): # If there's no "finally", "else" will be the last block
			node.orelse[-1] = self.patch_statement(node.orelse[-1])

		else: # Otherwise, we are dealing with a regular "try/except" statement
			node.body[-1] = self.patch_statement(node.body[-1])

			for handler in node.handlers:
				handler.body[-1] = self.patch_statement(handler.body[-1])

		return node

	def handle_Match(
		self,
		node: ast.Match
	) -> ast.Match:
		for case in node.cases:
			case.body[-1] = self.patch_statement(case.body[-1])

		return node

	def handle_TypeAlias(
		self,
		node: ast.TypeAlias
	) -> ast.Return:

		value = ast.NamedExpr(
			target=node.name,
			value=ast.Call(
				func=ast.Attribute(
					value=ast.Name(id=self.typing_name, ctx=ast.Load()),
					attr='TypeAliasType',
					ctx=ast.Load()
				),
				args=[
					ast.Constant(value=node.name.id),
					node.value
				],
				keywords=[
					ast.keyword(
						arg='type_params',
						value=ast.Tuple(
							elts=[self.assign_type_param(param) for param in node.type_params],
							ctx=ast.Load()
						)
					)
				]
			)
		)

		return self.handle_Return(ast.Return(value=value))


	def assign_type_param(
		self,
		param: ast.type_param
	):
		return ast.NamedExpr(
			target=ast.Name(id=param.name, ctx=ast.Store()), # type: ignore
			value=param
		)

	def get_end_lineno(
		self,
		node: ast.AST
	) -> int:
		if not hasattr(node, "end_lineno"):
			end_lineno: int = getattr(node, "end_lineno", node.lineno)
			return end_lineno

		return node.end_lineno # type: ignore

	def change_ctx[expr: ast.expr](
		self,
		node: expr
	) -> expr:
		node = copy(node)
		setattr(node, "ctx", ast.Load())
		return node
