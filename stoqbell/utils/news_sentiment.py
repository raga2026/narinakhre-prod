"""Rule-based sentiment scoring for stored stock_news headlines (see
utils/stock_news.py) -- feeds utils.nns_score.compute_nns_score's
news_sentiment_bonus. Pure logic only, no DB access, same DB-free/DB-
orchestration split used throughout this codebase (see nns_score.py's own
docstring).

A small hand-built finance-vocabulary keyword lexicon, not a trained model
or a general-purpose NLP library -- same "explainable heuristic, no opaque
dependency" approach this codebase already uses for price_pattern.py's
chart-pattern detection and nns_score.py's own scoring curves. A generic
English sentiment library (e.g. VADER) is tuned for social-media text, not
financial headlines -- "beats estimates", "block deal", "stake sale" carry
specific meaning here a general lexicon has no notion of, and pulling in a
model dependency for this would be disproportionate to what's a
supplementary, capped-impact scoring signal (see NEWS_SENTIMENT_BONUS_MAX
in nns_score.py).

Keyword-based and not phrase-order- or negation-aware -- a headline's score
is just (positive keyword matches - negative keyword matches), normalized
to -1..1. This will misread sarcasm or negation ("fails to fall despite
X"). Acceptable for a signal worth at most NEWS_SENTIMENT_BONUS_MAX points
out of 10; not acceptable if this were the primary scoring signal.
"""
import re

# Case-insensitive whole-word/phrase matches against Indian-market financial
# headline style (Screener/Moneycontrol/Economic Times phrasing), not a
# generic-English sentiment wordlist. Deliberately short phrases (not
# single ambiguous words like "high"/"low" on their own) to cut down on
# false positives against ordinary factual headlines.
POSITIVE_KEYWORDS = [
    'surge', 'surges', 'surged', 'surging', 'rally', 'rallies', 'rallied', 'rallying',
    'soar', 'soars', 'soared', 'soaring', 'jump', 'jumps', 'jumped', 'jumping',
    'rise', 'rises', 'rose', 'risen', 'rising', 'gain', 'gains', 'gained', 'gaining',
    'upgrade', 'upgrades', 'upgraded', 'outperform', 'buy rating', 'accumulate rating',
    'record high', 'all-time high', '52-week high', 'life high',
    'beats estimates', 'beat estimates', 'beats expectations', 'tops estimates',
    'profit jump', 'profit surge', 'profit soars', 'profit rises', 'profit up',
    'strong growth', 'robust growth', 'strong demand', 'record profit', 'record revenue',
    'wins order', 'wins contract', 'bags order', 'bags contract', 'secures order',
    'expansion plan', 'capacity expansion', 'buyback', 'bonus share', 'bonus issue',
    'special dividend', 'stake hike', 'raises stake', 'increases stake',
    'strong quarter', 'best quarter', 'beats street', 'multibagger', 'breakout',
    'target price hike', 'raises target', 'upgraded target', 'block deal buy',
]
NEGATIVE_KEYWORDS = [
    'plunge', 'plunges', 'plunged', 'plunging', 'crash', 'crashes', 'crashed', 'crashing',
    'slump', 'slumps', 'slumped', 'slumping', 'tumble', 'tumbles', 'tumbled', 'tumbling',
    'fall', 'falls', 'fell', 'fallen', 'falling', 'drop', 'drops', 'dropped', 'dropping',
    'decline', 'declines', 'declined', 'declining', 'slide', 'slides', 'slid', 'sliding',
    'downgrade', 'downgrades', 'downgraded', 'underperform', 'sell rating', 'reduce rating',
    'probe', 'raid', 'raids', 'fraud', 'scam', 'lawsuit', 'sued', 'penalty', 'penalised',
    'penalized', 'fine', 'fined', 'default', 'defaults', 'defaulted',
    'resign', 'resigns', 'resigned', 'resignation', 'steps down',
    'loss widens', 'net loss', 'profit falls', 'profit drops', 'profit declines',
    'profit plunges', 'weak quarter', 'disappointing quarter',
    'miss estimates', 'misses estimates', 'misses expectations', 'below estimates',
    'stake sale', 'sheds stake', 'sell-off', 'selloff', 'debt burden', 'debt pile',
    'insolvency', 'bankrupt', 'bankruptcy', 'layoff', 'layoffs', 'job cuts',
    'stock tanks', 'shares tank', 'shares plunge', '52-week low', 'multi-year low',
    'cuts target', 'lowers target', 'target price cut', 'block deal sell',
]

# A per-headline average this close to zero is treated as 'neutral' rather
# than 'positive'/'negative' -- e.g. one incidental keyword hit out of five
# stored headlines shouldn't flip the label away from neutral.
LABEL_NEUTRAL_BAND = 0.15

# Whole-word/phrase matches (\b...\b), not plain substring -- several
# keywords above are morphological variants of the same root ('surge' vs
# 'surges' vs 'surging'), and a plain `kw in text` substring check would
# double-count a single mention: 'surge' is itself a substring of
# 'surges', so both entries would match the one word. Word-boundary
# matching makes each listed form match only its own exact word, so
# listing every variant is both necessary (they don't share a common
# substring root the way 'surge'/'surges' happen to) and safe (no overlap
# between entries).
_POSITIVE_PATTERNS = [re.compile(r'\b' + re.escape(kw) + r'\b') for kw in POSITIVE_KEYWORDS]
_NEGATIVE_PATTERNS = [re.compile(r'\b' + re.escape(kw) + r'\b') for kw in NEGATIVE_KEYWORDS]


def score_headline(headline):
    """-1.0..1.0 for one headline: (positive matches - negative matches) /
    (positive matches + negative matches). 0.0 (neutral) if neither list
    matched, or if the matches exactly offset -- a headline mentioning
    both a positive and negative keyword nets out in between rather than
    double-counting in one direction."""
    if not headline:
        return 0.0
    text = headline.lower()
    positive_hits = sum(1 for pattern in _POSITIVE_PATTERNS if pattern.search(text))
    negative_hits = sum(1 for pattern in _NEGATIVE_PATTERNS if pattern.search(text))
    total_hits = positive_hits + negative_hits
    if total_hits == 0:
        return 0.0
    return (positive_hits - negative_hits) / total_hits


def compute_company_sentiment(headlines):
    """headlines: iterable of dicts/rows with a 'headline' key (see
    utils.stock_news.get_recent_news) -- typically the up-to-
    HEADLINES_PER_COMPANY most recently stored headlines for one company.

    Returns {'score': float -1.0..1.0, 'label': 'positive'/'negative'/'neutral',
    'headlines_scored': int, 'positive_count': int, 'negative_count': int}.
    score is the unweighted average of each headline's own score_headline()
    result -- every stored headline counts equally, no extra weight for
    the most recent one (get_recent_news/get_prominent_news already sort
    by published_at, but this only ever sees the already-pruned, already-
    small stored set -- see HEADLINES_PER_COMPANY in stock_news.py).

    No headlines at all -- never synced, or a universe-only company with
    nothing stored (stock_news is watchlist-scoped, see stock_news.py's
    own docstring) -- scores 0.0/'neutral'. This is the same score a
    genuine wash of positive-vs-negative headlines produces; this is a
    coarse supplementary signal, not a claim that 'no data' and 'a real
    wash' can be told apart from a free RSS feed."""
    headlines = list(headlines or [])
    if not headlines:
        return {'score': 0.0, 'label': 'neutral', 'headlines_scored': 0, 'positive_count': 0, 'negative_count': 0}

    per_headline_scores = [score_headline(h.get('headline')) for h in headlines]
    positive_count = sum(1 for s in per_headline_scores if s > 0)
    negative_count = sum(1 for s in per_headline_scores if s < 0)
    avg_score = sum(per_headline_scores) / len(per_headline_scores)

    if avg_score > LABEL_NEUTRAL_BAND:
        label = 'positive'
    elif avg_score < -LABEL_NEUTRAL_BAND:
        label = 'negative'
    else:
        label = 'neutral'

    return {
        'score': round(avg_score, 3), 'label': label,
        'headlines_scored': len(headlines),
        'positive_count': positive_count, 'negative_count': negative_count,
    }
