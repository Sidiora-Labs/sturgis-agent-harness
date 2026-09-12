---
title: "Author a canonical tool"
description: "Register bounded schemas and execute only through the real scheduler callback."
---

Every model tool uses the same canonical call interface. Registration defines the tool's argument and result schemas, version, required capabilities, read-only declaration, timeout and output bound. The executor is asynchronous and receives validated arguments plus an execution context.

```python
from agent.protocol import ToolCall
from agent.tool_runner import ToolDispatcher, ExecutorResult


async def tool_integration_example(journal, scheduler, scope, request_id):
    shape = {
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": 256}},
        "required": ["text"], "additionalProperties": False,
    }

    async def echo(arguments, context):
        return ExecutorResult({"text": arguments["text"]}, effects="none")

    dispatcher = ToolDispatcher(journal, authorize=scheduler.authorize)
    dispatcher.register(
        "example.echo", echo, input_schema=shape, output_schema=shape,
        required_capabilities=["echo.use"], read_only=True, timeout=1,
    )
    work = scheduler.submit(
        scope, "example", {}, capabilities=["echo.use"],
        resources={"tool_calls": 1, "elapsed_ms": 1000})
    work = scheduler.admit(scope, work.id)
    result = await dispatcher.invoke(
        ToolCall("example.echo", {"text": "hello"}), scope=scope,
        work_id=work.id, generation=work.value["generation"],
        request_id=request_id,
    )
    scheduler.reconcile_usage(scope)
    scheduler.finish(scope, work.id, work.value["generation"])
    return result
```

The embedding host invokes this function with the actual shared Journal and Scheduler described in [scheduler setup](../core/scheduler.md), a trusted scope, an available admission slot and a fresh request identity. It creates one demonstration work item. Recover an uncertain invocation through its original dispatcher/work/generation identity rather than rerunning this setup with a new work item. The echo has no external effect, so `effects="none"` is truthful. An effectful backend must report what it can actually observe. Defining the function alone performs no registration or execution.

## Claim before I/O

The dispatcher generates action, attempt and claim identities. It records intent and calls authorization in the same transaction. The grant contains trusted work/generation fields, registry capabilities, and a worst-case tool-call/time reservation. Model arguments cannot replace those values.

Schemas support bounded local references; remote schema retrieval is rejected. Avoid an unrestricted `{}` schema for a capability with meaningful parameters. Validate output as well as input.

An executor must preserve cancellation semantics, bound its I/O, and keep secret-bearing errors out of public projections. Delegated execution additionally requires explicit support for scheduler-provided workspace constraints. Never mark an arbitrary executor constraint-compatible without enforcing them. Continue with [effects and reconciliation](tool-effects.md).

## Implementation sources

- [agent/tool_runner/__init__.py](/reference/files/agent/tool_runner/__init__.py/)
- [agent/plugin_sdk/__init__.py](/reference/files/agent/plugin_sdk/__init__.py/)
- [agent/protocol/__init__.py](/reference/files/agent/protocol/__init__.py/)
- [agent/scheduler/__init__.py](/reference/files/agent/scheduler/__init__.py/)

[Developer manual](../index.md)
