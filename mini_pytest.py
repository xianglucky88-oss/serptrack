"""零依赖的 pytest 风格最小运行器（环境无 pip/pytest 时的后备）。

支持本仓库测试实际使用的特性：test_* 函数发现、assert 重写由普通 AssertionError
承载、tmp_path 夹具、pytest.mark.parametrize、pytest.raises。
若环境安装了真正的 pytest，请直接使用 `python -m pytest`。
"""
import inspect
import pathlib
import sys
import tempfile
import traceback
import types


class _Mark:
    def parametrize(self, argnames, argvalues):
        if isinstance(argnames, str):
            names = [a.strip() for a in argnames.split(",")]
        else:
            names = list(argnames)

        def decorator(fn):
            cases = []
            for val in argvalues:
                cases.append(val if isinstance(val, tuple) else (val,))
            fn.__parametrize__ = (names, cases)
            return fn
        return decorator

    def skip(self, reason=""):
        def decorator(fn):
            fn.__skip__ = reason or True
            return fn
        return decorator


class _Raises:
    def __init__(self, exc):
        self.exc = exc
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.exc.__name__}")
        if not issubclass(exc_type, self.exc):
            return False
        self.value = exc
        return True


pytest = types.ModuleType("pytest")
pytest.mark = _Mark()
pytest.raises = _Raises


def _fixtures_for(fn):
    sig = inspect.signature(fn)
    kwargs = {}
    for name in sig.parameters:
        if name == "tmp_path":
            kwargs[name] = pathlib.Path(tempfile.mkdtemp(prefix="mini-pytest-"))
        elif name == "con":
            from serptrack import store
            kwargs[name] = store.connect(str(pathlib.Path(tempfile.mkdtemp()) / "t.db"))
    return kwargs


def _close_fixtures(kwargs):
    for val in kwargs.values():
        close = getattr(val, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def run(test_dir: pathlib.Path) -> int:
    sys.modules.setdefault("pytest", pytest)
    # 让测试里的 `import pytest` 拿到兼容 shim
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    sys.modules["pytest"] = pytest
    # 让测试模块能 import 同目录的 conftest_support
    sys.path.insert(0, str(test_dir))

    passed = failed = skipped = 0
    failures = []
    for path in sorted(test_dir.glob("test_*.py")):
        module = types.ModuleType(path.stem)
        module.__file__ = str(path)
        try:
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"),
                 module.__dict__)
        except Exception:
            failed += 1
            failures.append((str(path), "<collection>", traceback.format_exc()))
            continue
        for name, fn in sorted(vars(module).items()):
            if not name.startswith("test_") or not callable(fn):
                continue
            cases = getattr(fn, "__parametrize__", None)
            if cases:
                argnames, argvalues = cases
                iterable = [dict(zip(argnames, vals)) for vals in argvalues]
            else:
                iterable = [{}]
            for params in iterable:
                label = f"{path.name}::{name}" + (
                    f"[{','.join(map(str, params.values()))}]" if params else "")
                if getattr(fn, "__skip__", False):
                    skipped += 1
                    continue
                kwargs = _fixtures_for(fn)
                kwargs.update(params)
                try:
                    fn(**kwargs)
                    passed += 1
                except Exception:
                    failed += 1
                    failures.append((label, kwargs, traceback.format_exc()))
                finally:
                    _close_fixtures(kwargs)

    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    for label, kwargs, tb in failures:
        print("=" * 70)
        print("FAIL", label)
        print(tb)
    return 1 if failed else 0


if __name__ == "__main__":
    root = pathlib.Path(__file__).resolve().parent
    sys.exit(run(root / "tests"))
