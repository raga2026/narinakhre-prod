import base64
import io

from PIL import Image

from utils.price_pattern import compute_projection_targets
from utils.suggestion_chart import build_prediction_chart_data_uri


def _decode(data_uri):
    assert data_uri.startswith('data:image/png;base64,')
    raw = base64.b64decode(data_uri.split(',', 1)[1])
    return Image.open(io.BytesIO(raw))


def test_returns_a_real_decodable_png_for_an_extrapolated_suggestion():
    projection = compute_projection_targets(100.0, 105.0, None)
    uri = build_prediction_chart_data_uri(100.0, projection, stop_loss_price=97.0)
    img = _decode(uri)
    assert img.format == 'PNG'
    assert img.size[0] > 0 and img.size[1] > 0


def test_returns_a_real_decodable_png_for_a_pattern_based_suggestion():
    projection = compute_projection_targets(100.0, 130.0, 'head_and_shoulders_bottom')
    uri = build_prediction_chart_data_uri(100.0, projection, stop_loss_price=95.0)
    _decode(uri)  # doesn't raise


def test_works_without_a_stop_loss_price():
    projection = compute_projection_targets(100.0, 105.0, None)
    uri = build_prediction_chart_data_uri(100.0, projection, stop_loss_price=None)
    _decode(uri)  # doesn't raise


def test_none_when_no_projection():
    assert build_prediction_chart_data_uri(100.0, {}, stop_loss_price=97.0) is None
    assert build_prediction_chart_data_uri(100.0, None, stop_loss_price=97.0) is None


def test_none_when_no_buy_price():
    projection = compute_projection_targets(100.0, 105.0, None)
    assert build_prediction_chart_data_uri(None, projection, stop_loss_price=97.0) is None


def test_handles_a_downward_move_without_crashing():
    projection = compute_projection_targets(100.0, 95.0, None)
    uri = build_prediction_chart_data_uri(100.0, projection, stop_loss_price=92.0)
    _decode(uri)
