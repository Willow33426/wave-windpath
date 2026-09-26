"""대회 서버 Nginx의 /api/ 접두사 제거 프록시와 라우트 호환성을 확인한다."""

import ast
from pathlib import Path


def test_public_and_proxy_internal_routes_are_registered() -> None:
    source = Path("app/main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    paths = {
        decorator.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "get"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
            and isinstance(decorator.args[0].value, str)
        )
    }

    assert "/api/health" in paths
    assert "/health" in paths
    assert "/api/observations" in paths
    assert "/observations" in paths
