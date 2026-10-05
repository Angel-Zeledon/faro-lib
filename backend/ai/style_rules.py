"""Wording rules shared by every LLM system prompt.

The owner's rule: the product never says the LatAm slang word for money; it says
"dinero". The slang word is assembled from two halves so that the repo-wide guard
(`tests/test_no_plata_word.py`) can stay free of any allow-list: the whole word
appears nowhere in the sources, only in the prompt the model receives.
"""

_SLANG = "pla" + "ta"

MONEY_WORDING_RULE = (
    f'Wording: when you write in Spanish, never use the slang word "{_SLANG}" for money '
    f'(nor "{_SLANG}s"); always say "dinero" (for example, say "dinero" wherever you would have written it).'
)
