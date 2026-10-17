"""The validator between generated SQL and the ops database (AIENG-57).

One of four layers (prompt, this, the reader role, read-only transaction limits); each must stop
a bad query on its own. Rules, in order:

1. it parses as PostgreSQL, and is exactly one statement;
2. it is a SELECT (or a set operation of SELECTs, or WITH … SELECT) with no write, DDL, command,
   `INTO` or locking clause anywhere inside it;
3. every table is on the allow-list (schema-qualified), or a name the query defines itself (a CTE);
4. every column is an allow-listed column of the tables it reads, or a name the query defines;
5. no system or denied function;
6. a numeric LIMIT, added or lowered to the row cap.

The query that runs is the one regenerated from the parse tree, never the original text.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from incidentops_ai.ops.catalogue import ALLOWED
from incidentops_ai.ops.database import MAX_ROWS

MAX_SQL_CHARS = 4000
SET_OPERATIONS = (exp.Union, exp.Intersect, exp.Except)
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Copy,
    exp.Set,
    exp.Into,
    exp.Lock,
    exp.TruncateTable,
    exp.Grant,
)
DENIED_PREFIXES = ("pg_", "lo_", "dblink", "txid_", "inet_")
DENIED_FUNCTIONS = {
    # who and where the query runs: nothing a question about operations needs
    "current_user",
    "session_user",
    "current_role",
    "user",
    "current_database",
    "current_catalog",
    "current_schema",
    "current_schemas",
    "set_config",
    "current_setting",
    "query_to_xml",
    "table_to_xml",
    "has_table_privilege",
    "version",
}


class SqlRefused(Exception):
    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(f"{rule}: {detail}")
        self.rule, self.detail = rule, detail


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def validate(sql: str, *, max_rows: int = MAX_ROWS) -> str:
    """The safe SQL to run, or SqlRefused naming the rule it broke."""
    if not sql or not sql.strip() or len(sql) > MAX_SQL_CHARS:
        raise SqlRefused("unparseable", "empty or too long")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError:
        raise SqlRefused("unparseable", "not valid PostgreSQL") from None
    if len(statements) != 1:
        raise SqlRefused("one_statement", f"{len(statements)} statements")
    # PostgreSQL folds unquoted names to lower case; a quoted name keeps its case.
    tree = normalize_identifiers(statements[0], dialect="postgres")

    if not isinstance(tree, (exp.Select, *SET_OPERATIONS)):
        raise SqlRefused("select_only", f"a {type(tree).__name__} statement")
    if forbidden := next(tree.find_all(*FORBIDDEN_NODES), None):
        raise SqlRefused("select_only", f"contains {type(forbidden).__name__}")

    defined = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    read: set[str] = set()
    for table in tree.find_all(exp.Table):
        if not table.name:  # a function in FROM, checked with the functions below
            continue
        qualified = f"{table.db}.{table.name}" if table.db else table.name
        if table.catalog:
            qualified = f"{table.catalog}.{qualified}"
        if not table.db and table.name in defined:
            continue
        if qualified not in ALLOWED:
            raise SqlRefused("table_not_allowed", qualified)
        read.add(qualified)

    columns = set().union(*(ALLOWED[t] for t in read)) if read else set()
    names = {a.alias for a in tree.find_all(exp.Alias) if a.alias}
    names |= {a.alias_or_name for a in tree.find_all(exp.TableAlias)}
    names |= {c.name for alias in tree.find_all(exp.TableAlias) for c in alias.columns}
    for column in tree.find_all(exp.Column):
        if column.name and column.name not in columns and column.name not in names:
            raise SqlRefused("column_not_allowed", column.name)

    for function in tree.find_all(exp.Func):
        name = _function_name(function)
        if name.startswith(DENIED_PREFIXES) or name in DENIED_FUNCTIONS:
            raise SqlRefused("function_not_allowed", name)

    if isinstance(tree, SET_OPERATIONS):
        tree = exp.select("*").from_(tree.subquery("q"))
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_rows)
    else:
        value = limit.expression
        if not isinstance(value, exp.Literal) or not value.is_int:
            raise SqlRefused("limit_not_literal", "LIMIT must be a number")
        if int(value.this) > max_rows:
            tree = tree.limit(max_rows)
    return tree.sql(dialect="postgres")
