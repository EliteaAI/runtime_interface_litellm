"""Method.get_system_user_token must delegate the pick to admin's get_project_system_token RPC (#6489).

Rotation (in admin) decides which system-user 'api' tokens stay alive. Picking locally here drifted
from that rule; the single owner is admin_get_project_system_token.

Run standalone (no pylon runtime needed):
    python3 tests/test_system_token_pick.py
"""

import ast
import os
import types
import unittest


class FakeRpc:
    def __init__(self, result="jwt-9"):
        self.result = result
        self.calls = []

    def timeout(self, _seconds):
        return self

    def admin_get_project_system_token(self, project_id, **kwargs):
        self.calls.append((project_id, kwargs))
        return self.result


def _load(rpc):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "methods", "tools.py"), encoding="utf-8") as fh:
        source = fh.read()
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "Method")
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "get_system_user_token")
    node.decorator_list = []
    # No 'auth' in the namespace: touching the token table here would raise NameError
    namespace = {"context": types.SimpleNamespace(rpc_manager=rpc)}
    exec(compile(ast.Module([node], []), "tools", "exec"), namespace)  # noqa: S102
    return lambda *a, **kw: namespace["get_system_user_token"](None, *a, **kw)


class SystemTokenPickTest(unittest.TestCase):
    def test_delegates_to_admin_rpc(self):
        rpc = FakeRpc()
        self.assertEqual(_load(rpc)(5), "jwt-9")
        self.assertEqual(rpc.calls, [(5, {"create_if_not_exists": True})])

    def test_passes_create_flag_through(self):
        rpc = FakeRpc(result=None)
        self.assertIsNone(_load(rpc)(5, create_if_not_exists=False))
        self.assertEqual(rpc.calls, [(5, {"create_if_not_exists": False})])


if __name__ == "__main__":
    unittest.main()
