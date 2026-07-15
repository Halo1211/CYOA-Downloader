import builtins
import dis
import importlib
import inspect


def _missing_load_globals(func):
    missing = []
    for ins in dis.get_instructions(func):
        if ins.opname in {"LOAD_GLOBAL", "LOAD_NAME"}:
            name = ins.argval
            if name not in func.__globals__ and not hasattr(builtins, name):
                missing.append(name)
    return sorted(set(missing))


def test_v466_setup_ui_capture_is_available_after_bootstrap():
    import cyoa_downloader_app.runtime.surface  # noqa: F401 - triggers bootstrap/resync
    from cyoa_downloader_app.gui import final_behaviors

    assert "_V466_PREVIOUS_SETUP_UI" in final_behaviors._v466_setup_ui.__globals__
    assert callable(final_behaviors._v466_setup_ui.__globals__["_V466_PREVIOUS_SETUP_UI"])


def test_patch_modules_have_required_cross_patch_globals_after_final_resync():
    import cyoa_downloader_app.runtime.surface  # noqa: F401 - triggers final resync

    modules = [
        "cyoa_downloader_app.gui.final_behaviors",
        "cyoa_downloader_app.gui.final_behaviors",
        "cyoa_downloader_app.gui.final_behaviors",
        "cyoa_downloader_app.gui.final_behaviors",
        "cyoa_downloader_app.gui.final_behaviors",
        "cyoa_downloader_app.gui.final_behaviors",
    ]
    failures = []
    for module_name in modules:
        mod = importlib.import_module(module_name)
        for name, obj in vars(mod).items():
            if inspect.isfunction(obj) and obj.__module__ == module_name:
                missing = _missing_load_globals(obj)
                if missing:
                    failures.append((module_name, name, missing))
    assert failures == []


