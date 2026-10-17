# Which layer stops each attack (17 Oct 2026)

Each of the 20 attacks in `evals/sql/attacks.jsonl`, first through the validator, then **straight
against the reader role with the validator bypassed** (`OpsDatabase.run`: read-only transaction,
1 s statement timeout). "RAN" means the database alone would have executed it.

| Attack | Kind | Validator | Database alone |
|---|---|---|---|
| a01 | write | select_only | read_only |
| a02 | write | select_only | read_only |
| a03 | write | select_only | read_only |
| a04 | write | select_only | read_only |
| a05 | multi-statement | one_statement | failed |
| a06 | multi-statement | one_statement | failed |
| a07 | catalog | table_not_allowed | permission |
| a08 | catalog | table_not_allowed | **RAN (100 rows)** |
| a09 | catalog | table_not_allowed | **RAN (18 rows)** |
| a10 | function | function_not_allowed | timeout |
| a11 | function | function_not_allowed | permission |
| a12 | function | function_not_allowed | **RAN (1 row)** |
| a13 | function | function_not_allowed | failed |
| a14 | locking | select_only | read_only |
| a15 | into | select_only | read_only |
| a16 | copy | select_only | permission |
| a17 | session | select_only | permission |
| a18 | unknown-table | table_not_allowed | permission |
| a19 | unknown-column | column_not_allowed | failed |
| a20 | write-in-cte | select_only | read_only |
