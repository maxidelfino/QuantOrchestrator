# Judgment Day Protocol

## Goal

Run adversarial review with two blind judges, synthesize findings, fix confirmed issues, and re-judge.

## Flow

1. Define exact scope.
2. Launch Judge A and Judge B in parallel.
3. Wait for both.
4. Classify findings:
   - confirmed
   - suspect A-only
   - suspect B-only
   - contradiction
5. Ask before fixing confirmed issues when the protocol requires it.
6. Use a separate fix agent.
7. Re-judge after confirmed fixes.

## Severity Guidance

- `CRITICAL` — serious correctness, safety, data-loss, security or execution risk
- `WARNING (real)` — realistic production issue worth fixing
- `WARNING (theoretical)` — edge case to report, not block on
- `SUGGESTION` — improvement, cleanup or maintainability note

## Outputs

- verdict table
- fixes applied
- approval or escalation state
