import io
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from csv_parser import parse_reserve_csv

HEADERS = (
    "Component,Replacement Budget,Design Life,Current Age,Replacement Year,"
    "Current Replacement Cost,Maintenance Budget,Maintenance Life Cycle,"
    "Remaining Maintenance Life,Maintenance Year,Current Maintenance Cost\n"
)


def _csv(rows: str) -> io.BytesIO:
    buf = io.BytesIO((HEADERS + rows).encode("utf-8"))
    buf.name = "test.csv"
    return buf


def test_replacement_only_component():
    result = parse_reserve_csv(_csv(
        "Roof - Asphalt Shingle,Yes,25,1,25,16500,No,,,,\n"
    ))
    assert len(result) == 1
    r = result[0]
    assert r["Property Components"] == "Roof - Asphalt Shingle"
    assert r["Replacement Desi"] == 25.0
    assert r["Replacement Curr"] == 1.0
    assert r["Replacement Year"] == 25.0
    assert r["Replacement Cost"] == 16500.0
    assert r["Maintenance Desi"] == 0.0
    assert r["Maintenance Cost"] == 0.0


def test_maintenance_only_component():
    result = parse_reserve_csv(_csv(
        "Exterior Painting,No,,,,,Yes,8,7,2,12000\n"
    ))
    assert len(result) == 1
    r = result[0]
    assert r["Maintenance Desi"] == 8.0
    assert r["Maintenance Year"] == 2.0
    assert r["Maintenance Cost"] == 12000.0
    assert r["Replacement Cost"] == 0.0
    assert r["Replacement Desi"] == 0.0


def test_both_tracks_active():
    result = parse_reserve_csv(_csv(
        "Fence - Wood,Yes,20,3,18,4200,Yes,5,3,3,2000\n"
    ))
    assert len(result) == 1
    r = result[0]
    assert r["Replacement Cost"] == 4200.0
    assert r["Replacement Desi"] == 20.0
    assert r["Maintenance Cost"] == 2000.0
    assert r["Maintenance Desi"] == 5.0


def test_both_no_budget_dropped():
    result = parse_reserve_csv(_csv(
        "Foundation,No,,,,,No,,,,\n"
    ))
    assert result == []


def test_empty_file_returns_empty():
    result = parse_reserve_csv(io.BytesIO(b""))
    assert result == []


def test_missing_required_columns_returns_empty():
    buf = io.BytesIO(b"Component,Design Life\nRoof,25\n")
    buf.name = "test.csv"
    result = parse_reserve_csv(buf)
    assert result == []


def test_column_names_case_insensitive_and_whitespace():
    headers = (
        " COMPONENT , REPLACEMENT BUDGET , DESIGN LIFE , CURRENT AGE ,"
        " REPLACEMENT YEAR , CURRENT REPLACEMENT COST , MAINTENANCE BUDGET ,"
        " MAINTENANCE LIFE CYCLE , REMAINING MAINTENANCE LIFE ,"
        " MAINTENANCE YEAR , CURRENT MAINTENANCE COST \n"
    )
    buf = io.BytesIO((headers + "Asphalt,Yes,25,6,20,112000,Yes,9,3,4,56000\n").encode())
    buf.name = "test.csv"
    result = parse_reserve_csv(buf)
    assert len(result) == 1
    assert result[0]["Replacement Desi"] == 25.0
    assert result[0]["Maintenance Desi"] == 9.0


def test_multiple_components_correct_count():
    result = parse_reserve_csv(_csv(
        "Roof,Yes,25,1,25,16500,No,,,,\n"
        "Fence,Yes,20,3,18,4200,Yes,5,3,3,2000\n"
        "Foundation,No,,,,,No,,,,\n"
    ))
    assert len(result) == 2
    assert result[0]["Property Components"] == "Roof"
    assert result[1]["Property Components"] == "Fence"
