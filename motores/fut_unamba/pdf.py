"""Rellena la plantilla oficial recibida sin reconstruir su diseño."""
from datetime import date
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

TEMPLATE = Path(__file__).parent / 'assets' / 'FUT UNAMBA.pdf'
FONT_PATH = Path('C:/Windows/Fonts/arial.ttf')
FONT = 'FutArial' if FONT_PATH.exists() else 'Helvetica'
if FONT == 'FutArial':
    pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))


def _lines(value, width, size, max_lines):
    words = str(value or '').split()
    lines, current = [], ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        if pdfmetrics.stringWidth(candidate, FONT, size) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        raise ValueError('Un texto no cabe en el formato oficial. Acórtalo antes de generar el FUT.')
    return lines


def _write(c, value, x, y, width, *, size=8.5, max_lines=1, leading=11):
    if not value:
        return
    for index, line in enumerate(_lines(value, width, size, max_lines)):
        c.setFont(FONT, size)
        c.drawString(x, y - index * leading, line)


def _summary_in_box(c, value):
    lines = _lines(value, 224, 8.5, 2)
    first_baseline = 668
    c.setFont(FONT, 8.5)
    for index, line in enumerate(lines):
        c.drawString(315, first_baseline - index * 14, line)


def render(data):
    if not TEMPLATE.is_file():
        raise FileNotFoundError('Falta la plantilla oficial FUT UNAMBA.pdf.')
    original = PdfReader(str(TEMPLATE))
    if len(original.pages) != 1:
        raise ValueError('Se esperaba una plantilla FUT de una página.')
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(float(original.pages[0].mediabox.width),
                                         float(original.pages[0].mediabox.height)))
    _summary_in_box(c, data.get('resumen'))
    _write(c, data.get('destinatario'), 101, 612, 416)
    _write(c, data.get('cargo'), 101, 592, 416)
    _write(c, data.get('nombre'), 55, 552, 470)
    _write(c, data.get('escuela'), 55, 508, 460)
    _write(c, data.get('dni'), 111, 483, 170)
    _write(c, data.get('codigo'), 425, 483, 100)
    _write(c, data.get('direccion'), 55, 451, 470)
    _write(c, data.get('distrito'), 115, 430, 85, size=8)
    _write(c, data.get('provincia'), 277, 430, 76, size=8)
    _write(c, data.get('departamento'), 427, 430, 100, size=8)
    _write(c, data.get('telefono'), 115, 405.5, 80, size=8)
    _write(c, data.get('celular'), 276, 405.5, 74, size=8)
    _write(c, data.get('correo'), 427, 405.5, 111, size=8)
    _write(c, data.get('fundamentacion'), 58, 331, 463, size=8.5, max_lines=4, leading=17)
    for index, item in enumerate((data.get('adjuntos') or [])[:12]):
        col, row = divmod(index, 6)
        _write(c, item, 77 + col * 248, 230 - row * 15.3, 205, size=8)
    _write(c, data.get('folios'), 290, 129, 235)
    today = date.today()
    months = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
              'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre')
    c.setFillColorRGB(1, 1, 1)
    c.rect(53, 46, 225, 35, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    place = (data.get('distrito') or 'Tamburco').strip().title()
    date_text = f'{place}, {today.day} de {months[today.month-1]} de {today.year}'
    if pdfmetrics.stringWidth(date_text, FONT, 8.5) > 216:
        raise ValueError('El lugar y fecha no caben en el formato. Abrevia el distrito.')
    c.setFont(FONT, 8.5)
    c.drawCentredString(165, 58, date_text)
    c.save()
    buffer.seek(0)
    page = original.pages[0]
    page.merge_page(PdfReader(buffer).pages[0])
    output = PdfWriter()
    output.add_page(page)
    result = BytesIO()
    output.write(result)
    return result.getvalue()
