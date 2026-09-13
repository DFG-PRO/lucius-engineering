"""Runtime package with lazy public exports.

Keeping these imports lazy avoids package-import cycles for lightweight runtime
helpers such as deterministic acceptance validation.
"""

_EXPORT_MODULES = {
    "DispatchCandidate": "lucius.runtime.schemas",
    "DispatchCandidateEvaluation": "lucius.runtime.schemas",
    "DispatchSelection": "lucius.runtime.schemas",
    "ExecutionAdapter": "lucius.runtime.adapters",
    "ExecutionAdapterResult": "lucius.runtime.schemas",
    "ExecutionRuntimeLoopResult": "lucius.runtime.schemas",
    "ExecutionRuntimeLoopService": "lucius.runtime.service",
    "ModelExecutionRouter": "lucius.runtime.router",
    "MultiProjectDispatcher": "lucius.runtime.dispatcher",
    "RuntimeExecutionContext": "lucius.runtime.schemas",
    "RuntimeExecutionProvider": "lucius.runtime.adapters",
    "RuntimeExecutionRequest": "lucius.runtime.schemas",
    "RuntimeExecutionResult": "lucius.runtime.schemas",
    "RuntimeLoopConfig": "lucius.runtime.schemas",
    "RuntimePlanReference": "lucius.runtime.schemas",
    "RuntimePlanningAdapter": "lucius.runtime.adapters",
    "RuntimeProviderRegistration": "lucius.runtime.schemas",
    "RuntimeProviderRegistry": "lucius.runtime.router",
    "RuntimeRoutingDecision": "lucius.runtime.schemas",
    "ScriptedExecutionAdapter": "lucius.runtime.adapters",
    "ScriptedRuntimePlanningAdapter": "lucius.runtime.adapters",
}

__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    from importlib import import_module

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
