"""Renders templates/pdf/export_invoice.html to a PDF (xhtml2pdf) and
uploads it to Supabase Storage. Kept separate from export_orders.py (pure
DB access) and app.py (routes) since this is the one piece that touches a
rendering engine plus a network upload.

templates/pdf/ uses its own plain Jinja2 Environment rather than going
through app.jinja_loader's per-theme ChoiceLoader (see app.py) -- an
invoice letterhead is one fixed internal document, not a themed
storefront page, so it doesn't belong under templates/themes/<theme>/.
"""
import io
import os

from jinja2 import Environment, FileSystemLoader
from xhtml2pdf import pisa

from supabase_storage import upload_bytes_to_supabase

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_pdf_jinja_env = Environment(loader=FileSystemLoader(os.path.join(_BASE_DIR, 'templates', 'pdf')))


def render_export_invoice_html(invoice, order, line_items, seller, bank):
    template = _pdf_jinja_env.get_template('export_invoice.html')
    return template.render(invoice=invoice, order=order, line_items=line_items, seller=seller, bank=bank)


def html_to_pdf_bytes(html):
    buf = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buf, encoding='UTF-8')
    if result.err:
        raise RuntimeError(f'xhtml2pdf reported {result.err} error(s) rendering the invoice')
    return buf.getvalue()


def generate_and_upload_export_invoice_pdf(invoice, order, line_items, seller, bank):
    """Returns the public Supabase Storage URL, or None if storage isn't
    configured / the upload failed (see upload_bytes_to_supabase) -- callers
    should still keep the invoice row in that case, same as every other
    optional-asset upload in this codebase."""
    html = render_export_invoice_html(invoice, order, line_items, seller, bank)
    pdf_bytes = html_to_pdf_bytes(html)
    safe_number = invoice['invoice_number'].replace('/', '-')
    path = f'export-invoices/{safe_number}.pdf'
    return upload_bytes_to_supabase(pdf_bytes, path, 'application/pdf')
