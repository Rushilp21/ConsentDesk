"""Generate the two short project PDFs and render them for inspection."""
from pathlib import Path
from html import escape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='DocTitle', fontName='Helvetica-Bold', fontSize=22, leading=27, spaceAfter=18, textColor=colors.HexColor('#102b31')))
styles.add(ParagraphStyle(name='Section', fontName='Helvetica-Bold', fontSize=12, leading=16, spaceBefore=10, spaceAfter=5, textColor=colors.HexColor('#126859')))
styles.add(ParagraphStyle(name='Copy', fontName='Helvetica', fontSize=10, leading=14, spaceAfter=6, alignment=TA_LEFT))

def build(name):
    source = ROOT / 'docs' / f'{name}.md'
    output = ROOT / 'output' / 'pdf' / f'{name}.pdf'
    output.parent.mkdir(parents=True, exist_ok=True)
    story = []
    for line in source.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        style = 'DocTitle' if line.startswith('# ') else 'Section' if line.startswith('## ') else 'Copy'
        content = line.removeprefix('## ').removeprefix('# ')
        bullet = content.startswith('- ')
        content = content.removeprefix('- ')
        story.append(Paragraph(escape(content), styles[style], bulletText='-' if bullet else None))
    def footer(canvas, doc):
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#667876'))
        canvas.drawString(42, 25, 'ConsentDesk  |  Personal research  |  9 October 2026')
        canvas.drawRightString(A4[0]-42, 25, str(doc.page))
    SimpleDocTemplate(str(output), pagesize=A4, rightMargin=46, leftMargin=48, topMargin=42, bottomMargin=44).build(story, onFirstPage=footer, onLaterPages=footer)
    qa = ROOT / 'tmp' / 'pdfs' / name
    qa.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(output))
    for i, page in enumerate(pdf):
        page.render(scale=1.6).to_pil().save(qa / f'page-{i+1}.png')
    print(f'{output}: {len(pdf)} pages')

if __name__ == '__main__':
    import sys
    for name in sys.argv[1:] or ['feature-plan', 'implemented-features']:
        build(name)
