# Kernel notes (SDK-neutral)

Details that matter when changing the kernel itself or writing unusual rules. No agent SDK is involved here.

## CEL (cel-python 0.5)

```python
import celpy
env = celpy.Environment()
prog = env.program(env.compile(expr))                # compile once per rule version; cache by expression
result = prog.evaluate({"record": celpy.json_to_cel(record_map), "reference": celpy.json_to_cel(reference_map)})
```

The real activation keys are the pack roots (for example `claim` and `plan`, or `order` and `terms`).

- Comparing with `None` raises `CELEvalError` (`no such overload`). Omit missing values from the maps and guard with `has()`.
- `has(a.b.c)` needs the map `a.b` to exist: build every intermediate map for declared paths, even when the leaf is missing.
- Integers and doubles do not mix (`int * double` fails); wrap numeric inputs in `double()`.
- `evaluate` may raise or return a `CELEvalError`; handle both.
- Macros `exists`, `all`, `has` and `size()` work; there is no `min`/`max`, so use the ternary `a < b ? a : b`.

## Async

- Planners fan out with `asyncio.gather` under `asyncio.wait_for(Settings.agent_deadline_s)`; an executor that times out or raises becomes an error report, not a failed answer.
- Executors must be async (`async handle`). Wrap a sync client with `asyncio.to_thread`.
- From sync code (some SDK tools), call `asyncio.run(planner.ask(...))` in a worker thread, never inside a running loop.
