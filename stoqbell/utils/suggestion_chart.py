"""Renders the small buy-to-target projection chart shown under each stock in
the daily suggestion email (see utils/suggestion_email.py). Pure image
rendering, no DB/network access -- takes the projection
utils.price_pattern.compute_projection_targets already computed and draws
it, nothing more.

Uses Pillow (already a dependency for product-photo handling elsewhere in
this codebase) rather than pulling in a charting library or an external
chart-image service -- this is one small, static, three-point line chart
(buy, mid-period, long-term -- see compute_projection_targets), not worth
either the added dependency or sending buy/target/stop-loss numbers to
a third party. Uploaded to Supabase Storage (same public 'products' bucket
the storefront's product photos use, under a stoqbell/charts/ path prefix)
and referenced by its public URL rather than embedded as a base64 data:
URI -- data: URIs render fine in Gmail/most webmail and mobile mail apps
but are stripped/blocked by Outlook desktop and some corporate mail
gateways (a well-known HTML-email limitation), which is why charts weren't
showing up for some recipients. The upload path is a hash of the chart's
own pixel content, not the suggestion id -- content-addressed, so an
identical chart (same buy/target/stop-loss numbers) reuses the same
uploaded file instead of growing storage unboundedly, and there's no risk
of the starters/large-cap/daily suggestion tables' independent id
sequences colliding on the same path. Every number the chart shows is
also printed as plain text/HTML nearby, so nothing is lost if a client
doesn't render the image at all.
"""
import hashlib
import io

from PIL import Image, ImageDraw, ImageFont

from supabase_storage import upload_bytes_to_supabase

CHART_WIDTH = 520
CHART_HEIGHT = 230
_MARGIN_LEFT = 60
_MARGIN_RIGHT = 20
_MARGIN_TOP = 22
_MARGIN_BOTTOM = 36

_LINE_COLOR = (34, 197, 94)       # #22c55e -- matches the sparkline on the marketing landing page
_STOP_LOSS_COLOR = (239, 68, 68)  # #ef4444
_AXIS_COLOR = (148, 163, 184)     # #94a3b8
_TEXT_COLOR = (30, 41, 59)        # #1e293b
_BG_COLOR = (255, 255, 255)


def _font():
    """A tiny built-in bitmap font -- no font file needs to ship with the
    app, and it's legible enough at this chart's small size. Falls back
    silently to PIL's absolute default if load_default() ever changes
    signature across a Pillow version."""
    try:
        return ImageFont.load_default(size=13)
    except TypeError:
        return ImageFont.load_default()


def build_prediction_chart_image_url(buy_price, projection, stop_loss_price=None):
    """Draws buy price (today) through the mid-period and long-term
    projected checkpoints (utils.price_pattern.compute_projection_targets'
    output -- each stock's own checkpoints, not a fixed calendar grid) as a
    simple line chart with a dashed stop-loss reference line, uploads it to
    Supabase Storage, and returns its public URL ready to drop straight
    into an <img src="..."> in suggestion_email.py's HTML.

    Returns None if projection is empty (compute_projection_targets returns
    {} when it has nothing to project from) -- no chart to draw for a
    suggestion missing buy/target prices."""
    if not buy_price or not projection or 'mid_period' not in projection:
        return None

    labels = ['Buy', projection['mid_period']['label'], projection['long_term']['label']]
    values = [buy_price, projection['mid_period']['price'], projection['long_term']['price']]

    all_values = list(values)
    if stop_loss_price:
        all_values.append(stop_loss_price)
    y_min = min(all_values) * 0.97
    y_max = max(all_values) * 1.05
    if y_max <= y_min:
        y_max = y_min + 1

    plot_w = CHART_WIDTH - _MARGIN_LEFT - _MARGIN_RIGHT
    plot_h = CHART_HEIGHT - _MARGIN_TOP - _MARGIN_BOTTOM
    n = len(values)

    def x_at(i):
        return _MARGIN_LEFT + (plot_w * i / (n - 1) if n > 1 else 0)

    def y_at(value):
        return _MARGIN_TOP + plot_h - (value - y_min) / (y_max - y_min) * plot_h

    img = Image.new('RGB', (CHART_WIDTH, CHART_HEIGHT), _BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = _font()

    # Baseline axis
    draw.line(
        [(_MARGIN_LEFT, _MARGIN_TOP + plot_h), (CHART_WIDTH - _MARGIN_RIGHT, _MARGIN_TOP + plot_h)],
        fill=_AXIS_COLOR, width=1,
    )

    if stop_loss_price:
        sl_y = y_at(stop_loss_price)
        for dash_x in range(_MARGIN_LEFT, CHART_WIDTH - _MARGIN_RIGHT, 8):
            draw.line([(dash_x, sl_y), (min(dash_x + 4, CHART_WIDTH - _MARGIN_RIGHT), sl_y)],
                      fill=_STOP_LOSS_COLOR, width=1)
        draw.text((CHART_WIDTH - _MARGIN_RIGHT, sl_y - 14), f'Stop-loss {stop_loss_price:g}',
                   fill=_STOP_LOSS_COLOR, font=font, anchor='ra')

    coords = [(x_at(i), y_at(v)) for i, v in enumerate(values)]
    if len(coords) > 1:
        draw.line(coords, fill=_LINE_COLOR, width=3, joint='curve')
    for i, (x, y) in enumerate(coords):
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=_LINE_COLOR, outline=_BG_COLOR, width=1)
        label = labels[i]
        value_text = f'{values[i]:g}'
        above = i % 2 == 0
        text_y = y - 20 if above else y + 8
        draw.text((x, text_y), value_text, fill=_TEXT_COLOR, font=font, anchor='ma')
        draw.text((x, _MARGIN_TOP + plot_h + 6), label, fill=_AXIS_COLOR, font=font, anchor='ma')

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    png_bytes = buf.getvalue()

    # Content-addressed path: identical chart content (same buy/target/
    # stop-loss numbers) reuses the same uploaded object instead of
    # growing storage unboundedly, and there's no dependency on a
    # suggestion id -- daily/starters/large-cap-bonus suggestions live in
    # three separate tables with independent id sequences, so an id-based
    # path could otherwise collide across them.
    content_hash = hashlib.sha256(png_bytes).hexdigest()[:20]
    return upload_bytes_to_supabase(png_bytes, f'stoqbell/charts/{content_hash}.png', 'image/png')
