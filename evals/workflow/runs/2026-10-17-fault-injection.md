# Workflow fault injection (2026-10-17)

Real PostgreSQL and checkpoints, the ticket MCP server over stdio, scripted models.

| Fault | Expected | Ended | Events | Tickets | Model calls made / recorded | Pass | Detail |
|---|---|---|---|---|---|---|---|
| tool timeout after the write | `actioned` | `actioned` | started → awaiting_approval → decided → failed → resumed → action_executed → completed | 1 | 2 / 2 | yes | first attempt: TicketTimeoutError; resume: created=False |
| duplicate write; repeated tool call | `actioned` | `actioned` | started → awaiting_approval → decided → action_executed → completed | 1 | 2 / 2 | yes | second write created=False; tool loop: loop_detected after 2 rounds, tool ran 1x |
| invalid state | `approved` | `approved` | started → awaiting_approval → decided → completed | 0 | 2 / 2 | yes | refused: resume of a paused run, malformed decision, second decision; gate still next: True |
| model failure | `awaiting_approval` | `awaiting_approval` | started → failed → resumed → awaiting_approval | 0 | 3 / 3 | yes | timeout: awaiting_approval via model_failed, no_answer; crash: failed, resumed to the gate |
| approval rejected | `rejected` | `rejected` | started → awaiting_approval → decided → completed | 0 | 2 / 2 | yes | decided by Amara Okafor: Wrong service. |

**MCP over stdio** (50 reads): server start 0.3 s, read median 0.64 ms, max 4.79 ms.
