import io
from unittest.mock import patch

from PIL import Image

from stoqbell.utils.price_pattern import compute_projection_targets
from stoqbell.utils.suggestion_chart import build_prediction_chart_image_url


def _fake_upload(captured):
    def _upload(binary_payload, path, content_type, bucket=None):
        captured['bytes'] = binary_payload
        captured['path'] = path
        captured['content_type'] = content_type
        return f'https://example.supabase.co/storage/v1/object/public/products/{path}'
    return _upload


def _build_and_capture(*args, **kwargs):
    captured = {}
    with patch('stoqbell.utils.suggestion_chart.upload_bytes_to_supabase', side_effect=_fake_upload(captured)):
        url = build_prediction_chart_image_url(*args, **kwargs)
    return url, captured


def test_uploads_a_real_decodable_png_for_an_extrapolated_suggestion():
    projection = compute_projection_targets(100.0, 105.0, None)
    url, captured = _build_and_capture(100.0, projection, stop_loss_price=97.0)
    assert url == f'https://example.supabase.co/storage/v1/object/public/products/{captured["path"]}'
    assert captured['content_type'] == 'image/png'
    assert captured['path'].startswith('stoqbell/charts/') and captured['path'].endswith('.png')
    img = Image.open(io.BytesIO(captured['bytes']))
    assert img.format == 'PNG'
    assert img.size[0] > 0 and img.size[1] > 0


def test_uploads_a_real_decodable_png_for_a_pattern_based_suggestion():
    projection = compute_projection_targets(100.0, 130.0, 'head_and_shoulders_bottom')
    url, captured = _build_and_capture(100.0, projection, stop_loss_price=95.0)
    assert url
    Image.open(io.BytesIO(captured['bytes']))  # doesn't raise


def test_works_without_a_stop_loss_price():
    projection = compute_projection_targets(100.0, 105.0, None)
    url, captured = _build_and_capture(100.0, projection, stop_loss_price=None)
    assert url
    Image.open(io.BytesIO(captured['bytes']))  # doesn't raise


def test_none_when_no_projection():
    assert build_prediction_chart_image_url(100.0, {}, stop_loss_price=97.0) is None
    assert build_prediction_chart_image_url(100.0, None, stop_loss_price=97.0) is None


def test_none_when_no_buy_price():
    projection = compute_projection_targets(100.0, 105.0, None)
    assert build_prediction_chart_image_url(None, projection, stop_loss_price=97.0) is None


def test_handles_a_downward_move_without_crashing():
    projection = compute_projection_targets(100.0, 95.0, None)
    url, captured = _build_and_capture(100.0, projection, stop_loss_price=92.0)
    assert url
    Image.open(io.BytesIO(captured['bytes']))


def test_returns_none_when_upload_fails():
    """upload_bytes_to_supabase returns None if Supabase isn't configured
    or the PUT fails -- the chart is simply omitted from the email (see
    suggestion_email.py's chart_block), not an error."""
    projection = compute_projection_targets(100.0, 105.0, None)
    with patch('stoqbell.utils.suggestion_chart.upload_bytes_to_supabase', return_value=None):
        url = build_prediction_chart_image_url(100.0, projection, stop_loss_price=97.0)
    assert url is None


def test_identical_chart_content_produces_the_same_upload_path():
    """Content-addressed path (sha256 of the PNG bytes) -- an identical
    chart (same buy/target/stop-loss numbers) reuses the same object
    instead of growing storage unboundedly."""
    projection = compute_projection_targets(100.0, 105.0, None)
    _, first = _build_and_capture(100.0, projection, stop_loss_price=97.0)
    _, second = _build_and_capture(100.0, projection, stop_loss_price=97.0)
    assert first['path'] == second['path']
