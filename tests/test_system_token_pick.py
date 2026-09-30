"""Method.get_system_user_token must hand out the newest 'api' token (#6489).

Rotation keeps the Vault token (the newest) plus the previous one and deletes the rest. Taking the
first row of the unordered token list handed out the older one, which the next rotation revokes.

Run standalone (no pylon runtime needed):
    python3 tests/test_system_token_pick.py
"""

import ast
import os
import textwrap
import types
import unittest


class FakeAuth:
    def __init__(self, tokens):
        self.tokens = tokens
        self.added = []

    def list_tokens(self, user_id):
        return list(self.tokens)

    def encode_token(self, token_id):
        return f"jwt-{token_id}"

    def add_token(self, user_id, name):
        self.added.append((user_id, name))
        return 999


def _load(auth):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "methods", "tools.py"), encoding="utf-8") as fh:
        source = fh.read()
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "Method")
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "get_system_user_token")
    node.decorator_list = []
    rpc_manager = types.SimpleNamespace(
        timeout=lambda _s: types.SimpleNamespace(admin_get_project_system_user=lambda pid: {"id": 7}),
    )
    namespace = {"auth": auth, "context": types.SimpleNamespace(rpc_manager=rpc_manager)}
    exec(compile(ast.Module([node], []), "tools", "exec"), namespace)  # noqa: S102
    return lambda *a, **kw: namespace["get_system_user_token"](None, *a, **kw)


def _tok(token_id, name="api"):
    return {"id": token_id, "name": name, "expires": None}


class SystemTokenPickTest(unittest.TestCase):
    def test_newest_token_wins_regardless_of_list_order(self):
        for order in ([_tok(5), _tok(9)], [_tok(9), _tok(5)]):
            self.assertEqual(_load(FakeAuth(order))(1), "jwt-9")

    def test_other_names_are_ignored(self):
        self.assertEqual(_load(FakeAuth([_tok(5), _tok(50, "my-ci")]))(1), "jwt-5")

    def test_missing_token_is_created(self):
        auth = FakeAuth([_tok(50, "my-ci")])
        self.assertEqual(_load(auth)(1), "jwt-999")
        self.assertEqual(auth.added, [(7, "api")])

    def test_missing_token_is_not_created_when_disabled(self):
        self.assertIsNone(_load(FakeAuth([]))(1, create_if_not_exists=False))


if __name__ == "__main__":
    unittest.main()
