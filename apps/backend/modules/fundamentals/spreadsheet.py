"""Archivos XLSX de intercambio; textos siempre explícitos, nunca fórmulas."""

import io

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell


def make_xlsx(rows, title):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(title)
    for row in rows:
        cells = []
        for value in row:
            cell = WriteOnlyCell(sheet, value=value)
            if isinstance(value, str):
                cell.data_type = "s"
            cells.append(cell)
        sheet.append(cells)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
