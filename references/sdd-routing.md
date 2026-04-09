# SDD Routing

## When to Use SDD

Route work to `sdd-orchestrator` when the request implies:

- new bot architecture
- multi-file feature work
- structural refactor
- non-trivial execution logic
- risky changes that need spec/design/verify flow

## Core Flow

`explore -> propose -> spec + design -> tasks -> apply -> verify -> archive`

## Defaults

- artifact store: `engram`
- execution mode: ask first time, then cache preference
- keep the main thread thin and summarize phases only
