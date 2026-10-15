"""Explicit cloud smoke: count the twelve generic consultancy tables."""
from enterprise_schema import TABLE_NAMES
from agents.tests.m3_sql_handler import execute_sql


def main() -> int:
    failed = False
    for name in TABLE_NAMES:
        result = execute_sql(f"SELECT COUNT(*) AS RowCount FROM dbo.[{name.lower()}]")
        print(name, result)
        failed |= "error" in result
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
