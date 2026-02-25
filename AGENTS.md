# Project Codex Memory - papermaps

## Purpose
This project follows strict software engineering discipline: small-step, testable, reviewable, and reversible delivery.

## Global Goal
- Implement requirements step by step in the current project.
- Deliver only one smallest meaningful step per iteration.
- Ensure each step is testable, reviewable, and git-committable.
- Avoid unauthorized broad refactors and unrelated edits.

## Mandatory Principles
1. Small-step iteration: one clear goal per step.
2. Plan before coding: output TODO / DoD / test plan first.
3. MVP first: verify first, optimize later.
4. Constrained scope: edit only directly related files.
5. Reversible changes: keep changes easy to revert.
6. Honest reporting: never fake test/run status.
7. Contract-first interfaces: document input/output/error semantics.
8. Observability: add actionable logs on key paths.
9. Explicit assumptions: environment/data/dependency assumptions must be stated.
10. No overengineering: no unnecessary architecture expansion.

## Required Per-Round Flow (strict order)
For every implementation round, follow this exact sequence:

1. 本步 DoD
2. 假设与风险
3. TODO（带路径）
4. 测试计划
5. 实现与改动摘要
6. 自检结果（DoD 对照）
7. 接口契约（如有）
8. 测试结果
9. 风险与下一步建议
10. Git 提交建议

Do not skip A-D before implementation.

## DoD Minimum Requirements
- Functional completion condition
- Acceptance method (commands + expected results)
- Test pass condition (happy path + at least one boundary/error path)
- Deliverable file list
- Explicit out-of-scope items

## TODO Rules
- Each TODO must include concrete path(s)
- Mark change type: 新增 / 修改 / 删除
- Mark relation: 直接相关 / 支撑性
- If expected touched files > 5, explain why before coding

## Test Rules
- Include happy-path and boundary/error tests
- If interface changes, verify error semantics
- If automated tests unavailable, provide manual verification steps

## Interface Contract Template (mandatory when interface changes)
- 接口名称
- 用途（为什么存在）
- 输入参数（参数名 / 类型 / 必填 / 默认值 / 含义）
- 返回值（类型 / 字段说明 / 成功语义）
- 错误处理（错误码/异常类型、触发条件、调用方处理方式）
- 边界条件（空值/非法值/超长值/并发/重复调用）
- 幂等性（如适用）
- 使用示例（输入与输出/行为）

## Observability Rules
On key paths, include:
- start log (input summary, no sensitive info)
- success log (result summary)
- failure log (error context + location)
- debug switch support if logging system exists

## Strict Prohibitions
- No fabricated test/run results
- No claiming pass when tests failed/skipped
- No unrelated file edits
- No unauthorized large refactors
- No silent interface behavior changes
- No pre-implementing future phases
- No missing error/boundary handling
- No fabricated environment assumptions

## Project-Specific Adjustments (papermaps)
- Default roadmap order:
  - Phase 0/1 first, then Phase 2, then Phase 3/4
- Default technical baseline:
  - Python + SQLite + OpenAlex API integration
- Dependency policy:
  - Do not add new dependency unless explicitly justified and approved
- Versioning and commit policy:
  - Use versioned commit note (e.g., `v0.1.1`)
  - Prefer annotated tags for release milestones
- Network/API policy:
  - Respect API limits and auth requirements, report failures transparently

## Interaction Rule
If user input is incomplete, proceed with the smallest executable plan, state assumptions explicitly, and continue instead of stalling.
