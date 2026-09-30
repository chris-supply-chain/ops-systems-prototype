"""Minimal .xlsx writer and reader on the standard library (zipfile + ElementTree).

Enough of the OOXML spreadsheet format to produce files Excel, Numbers and
LibreOffice open, and to read back what suppliers actually send: shared or
inline strings, numbers, booleans, sparse cells and merged title rows.
"""
import io
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _cell(ref, value, bold=False):
    style = ' s="1"' if bold else ""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{style}><v>{int(value)}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{text}</t></is></c>'


def _put(z, name, data):
    # a fixed entry timestamp: zipfile stamps the wall clock otherwise, and the bytes must not depend on build time
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, data)


def write_xlsx(sheets):
    """sheets: [{"name": str, "rows": [[...], ...], "bold_rows": {0}, "merges": ["A1:F1"], "widths": [12, ...]}]"""
    buf = io.BytesIO()
    z = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    _put(z, "[Content_Types].xml",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
               '<Default Extension="xml" ContentType="application/xml"/>'
               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
               '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
               + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                         f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                         for i in range(len(sheets)))
               + "</Types>")
    _put(z, "_rels/.rels",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
               '</Relationships>')
    _put(z, "xl/workbook.xml",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
               + "".join(f'<sheet name="{escape(s["name"])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                         for i, s in enumerate(sheets))
               + "</sheets></workbook>")
    _put(z, "xl/_rels/workbook.xml.rels",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                         for i in range(len(sheets)))
               + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               '</Relationships>')
    _put(z, "xl/styles.xml",
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
               '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
               '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
               '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
               '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
               '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
               '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
               '</styleSheet>')
    for i, sheet in enumerate(sheets):
        bold = sheet.get("bold_rows", set())
        rows_xml = []
        for r, row in enumerate(sheet["rows"]):
            cells = "".join(_cell(f"{col_letter(c)}{r + 1}", v, r in bold) for c, v in enumerate(row))
            rows_xml.append(f'<row r="{r + 1}">{cells}</row>')
        cols = ""
        if sheet.get("widths"):
            cols = "<cols>" + "".join(f'<col min="{k + 1}" max="{k + 1}" width="{wd}" customWidth="1"/>'
                                      for k, wd in enumerate(sheet["widths"])) + "</cols>"
        merges = ""
        if sheet.get("merges"):
            merges = f'<mergeCells count="{len(sheet["merges"])}">' + "".join(
                f'<mergeCell ref="{m}"/>' for m in sheet["merges"]) + "</mergeCells>"
        _put(z, f"xl/worksheets/sheet{i + 1}.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   f'{cols}<sheetData>{"".join(rows_xml)}</sheetData>{merges}</worksheet>')
    z.close()
    return buf.getvalue()


_REF = re.compile(r"([A-Z]+)(\d+)")


def read_xlsx(data):
    """Returns [(sheet_name, rows)] where rows are lists padded with None."""
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", NS)}
    out = []
    for sh in wb.find("m:sheets", NS).findall("m:sheet", NS):
        rid = sh.get(f"{{{NS['r']}}}id")
        path = target[rid].lstrip("/")
        path = path if path.startswith("xl/") else "xl/" + path
        root = ET.fromstring(z.read(path))
        grid = {}
        max_c = 0
        for c in root.iter(f"{{{NS['m']}}}c"):
            m = _REF.match(c.get("r"))
            ci, ri = col_index(m.group(1)), int(m.group(2)) - 1
            t = c.get("t")
            v = c.find("m:v", NS)
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            elif t == "s":
                val = shared[int(v.text)]
            elif t == "b":
                val = v.text == "1"
            elif t in ("str", "e"):
                val = v.text if v is not None else None
            elif v is not None and v.text is not None:
                num = float(v.text)
                val = int(num) if num.is_integer() else num
            else:
                val = None
            grid.setdefault(ri, {})[ci] = val
            max_c = max(max_c, ci + 1)
        rows = []
        for ri in range(max(grid) + 1 if grid else 0):
            cells = grid.get(ri, {})
            rows.append([cells.get(ci) for ci in range(max_c)])
        out.append((sh.get("name"), rows))
    return out
