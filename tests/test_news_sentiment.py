from stoqbell.utils.news_sentiment import compute_company_sentiment, score_headline


# --- score_headline ---------------------------------------------------------

def test_positive_headline_scores_positive():
    assert score_headline('Company profit surges 40% on strong demand, beats estimates') == 1.0


def test_negative_headline_scores_negative():
    assert score_headline('Company shares crash after fraud probe, CFO resigns') == -1.0


def test_headline_with_no_keyword_matches_scores_zero():
    assert score_headline('Company announces annual general meeting date') == 0.0


def test_headline_with_offsetting_keywords_scores_between():
    # One positive ('surge'), one negative ('probe') -- nets to 0, not
    # double-counted in either direction.
    assert score_headline('Stock surges even as company faces regulatory probe') == 0.0


def test_empty_or_none_headline_scores_zero():
    assert score_headline('') == 0.0
    assert score_headline(None) == 0.0


def test_matching_is_case_insensitive():
    assert score_headline('COMPANY SHARES PLUNGE ON WEAK QUARTER') == -1.0


# --- compute_company_sentiment -----------------------------------------------

def test_no_headlines_is_neutral():
    result = compute_company_sentiment([])
    assert result == {'score': 0.0, 'label': 'neutral', 'headlines_scored': 0, 'positive_count': 0, 'negative_count': 0}


def test_none_is_treated_the_same_as_empty_list():
    assert compute_company_sentiment(None) == compute_company_sentiment([])


def test_all_positive_headlines_score_positive_label():
    headlines = [
        {'headline': 'Company profit jumps 25% on strong quarter'},
        {'headline': 'Stock hits record high after wins order from major client'},
    ]
    result = compute_company_sentiment(headlines)
    assert result['score'] == 1.0
    assert result['label'] == 'positive'
    assert result['headlines_scored'] == 2
    assert result['positive_count'] == 2
    assert result['negative_count'] == 0


def test_all_negative_headlines_score_negative_label():
    headlines = [
        {'headline': 'Company shares tank after profit falls sharply'},
        {'headline': 'Stock downgraded amid fraud probe'},
    ]
    result = compute_company_sentiment(headlines)
    assert result['score'] == -1.0
    assert result['label'] == 'negative'
    assert result['positive_count'] == 0
    assert result['negative_count'] == 2


def test_mixed_headlines_average_out_to_a_smaller_magnitude():
    headlines = [
        {'headline': 'Company profit surges on strong growth'},
        {'headline': 'Company shares plunge on weak quarter'},
    ]
    result = compute_company_sentiment(headlines)
    assert result['score'] == 0.0
    assert result['label'] == 'neutral'
    assert result['positive_count'] == 1
    assert result['negative_count'] == 1


def test_small_nonzero_average_still_reads_as_neutral():
    # 19 neutral (no-keyword) headlines and one fully positive one averages
    # to 1.0 / 20 = 0.05, well inside LABEL_NEUTRAL_BAND -- a single
    # incidental keyword hit among many neutral headlines shouldn't flip
    # the label. (More headlines than stock_news.py's own
    # HEADLINES_PER_COMPANY cap ever stores per company -- this exercises
    # the label boundary itself, not a realistic stored-headline count.)
    headlines = [{'headline': 'Company files routine regulatory disclosure'} for _ in range(19)]
    headlines.append({'headline': 'Company stock gains in early trade'})
    result = compute_company_sentiment(headlines)
    assert -0.15 < result['score'] < 0.15
    assert result['label'] == 'neutral'
