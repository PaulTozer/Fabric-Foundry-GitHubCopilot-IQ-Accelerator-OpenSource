import pytest
from m3_sql_handler import _is_safe, _normalise_table_names


@pytest.mark.parametrize("sql", [
    "DROP\tTABLE ent_staff", "SELECT * INTO new_table FROM ent_staff",
    "SELECT 1; DELETE FROM ent_staff", "SELECT/**/1", "EXEC\nxp_cmdshell 'whoami'",
    "SELECT * FROM OPENROWSET('x','y','z')", "--comment\nSELECT 1",
])
def test_sql_rejects_writes_comments_multistatement(sql):
    assert not _is_safe(sql)[0]


def test_sql_readonly_and_consistent_generic_names():
    assert _is_safe("SELECT TOP 20 StaffName FROM ENT_Staff WHERE StaffName LIKE '%drop%'")[0]
    assert _is_safe("WITH p AS (SELECT ProjectID FROM ENT_Project) SELECT * FROM p")[0]
    assert "dbo.ent_staff" in _normalise_table_names("SELECT * FROM dbo.[ENT_Staff]")
