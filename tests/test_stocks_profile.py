"""Format-only validation for the Regular-signup profile fields
(utils/stocks_subscription.validate_stocks_profile) -- no OTP, no external
lookups, so this is pure and fully unit-testable."""
from datetime import date, timedelta

from stoqbell.utils.stocks_subscription import validate_stocks_profile, stocks_profile_is_complete


def _ok(**over):
    args = dict(phone_country_code='+91', phone='9876543210',
               date_of_birth='1990-05-05', location='Jabalpur', pincode='482001')
    args.update(over)
    return validate_stocks_profile(**args)


def test_valid_profile_returns_cleaned_dict_and_no_error():
    cleaned, error = _ok()
    assert error is None
    assert cleaned == {
        'phone_country_code': '+91', 'phone': '9876543210',
        'date_of_birth': '1990-05-05', 'location': 'Jabalpur', 'pincode': '482001',
    }


def test_phone_and_location_are_normalized():
    cleaned, error = _ok(phone='  98765-43210 ', location='  New   Delhi  ')
    assert error is None
    assert cleaned['phone'] == '9876543210'
    assert cleaned['location'] == 'New Delhi'


def test_country_code_must_start_with_plus():
    _, error = _ok(phone_country_code='91')
    assert error and 'country code' in error.lower()


def test_country_code_accepts_one_to_four_digits():
    assert _ok(phone_country_code='+1')[1] is None
    assert _ok(phone_country_code='+9999')[1] is None
    assert _ok(phone_country_code='+12345')[1] is not None


def test_phone_must_be_six_to_fourteen_digits():
    assert _ok(phone='12345')[1] is not None
    assert _ok(phone='123456')[1] is None
    assert _ok(phone='12345678901234')[1] is None
    assert _ok(phone='123456789012345')[1] is not None
    assert _ok(phone='98765abcd0')[1] is not None


def test_pincode_must_be_exactly_six_digits():
    assert _ok(pincode='4820')[1] is not None
    assert _ok(pincode='4820011')[1] is not None
    assert _ok(pincode='48200a')[1] is not None
    assert _ok(pincode='482001')[1] is None


def test_location_is_required_and_bounded():
    assert _ok(location='   ')[1] is not None
    assert _ok(location='x' * 121)[1] is not None
    assert _ok(location='x' * 120)[1] is None


def test_date_of_birth_must_be_a_valid_past_date():
    assert _ok(date_of_birth='not-a-date')[1] is not None
    assert _ok(date_of_birth=date.today().isoformat())[1] is not None
    assert _ok(date_of_birth=(date.today() + timedelta(days=1)).isoformat())[1] is not None
    assert _ok(date_of_birth='1850-01-01')[1] is not None  # absurdly old
    assert _ok(date_of_birth=(date.today() - timedelta(days=365 * 20)).isoformat())[1] is None


def test_stocks_profile_is_complete():
    full = {'phone_country_code': '+91', 'phone': '9876543210',
            'date_of_birth': '1990-05-05', 'location': 'Jabalpur', 'pincode': '482001'}
    assert stocks_profile_is_complete(full) is True
    assert stocks_profile_is_complete({**full, 'phone': None}) is False
    assert stocks_profile_is_complete({**full, 'location': '  '}) is False
    assert stocks_profile_is_complete({}) is False
