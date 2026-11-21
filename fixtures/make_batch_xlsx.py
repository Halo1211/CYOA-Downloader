"""Write XLSX inputs for the batch fixture (then run export_batch.py).
Run from the repo root: python fixtures/make_batch_xlsx.py"""
from pathlib import Path
import openpyxl

d = Path(__file__).parent / "batch" / "in"


def book(name, rows, extra_sheet=False):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    if extra_sheet:
        wb.create_sheet("Second").append(["url"])
        wb["Second"].append(["https://second.example/ignored/"])
    wb.save(d / name)


book("basic.xlsx", [
    ["URL", "Filename", "Mode"],
    ["https://a.example/cyoa/", "Alpha", "zip"],
    ["  https://b.example/x/  ", "", "website_folder"],
    ["not a url", "skip", "both"],
    [None, None, None],
    ["https://c.example/", 12345, "bogus"],
    ["https://d.example/", 1.5, True],
], extra_sheet=True)
book("link_dup.xlsx", [
    ["Title", "Link", "link", "Type"],
    ["First", "https://e.example/", "https://ignored.example/", "cyoap_vue_folder"],
    ["", "https://f.example/", "", ""],
])
book("nourl.xlsx", [["name", "mode"], ["x", "zip"]])
book("empty.xlsx", [])
print("ok")
