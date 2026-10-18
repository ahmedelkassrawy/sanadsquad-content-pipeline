"""Brand-rules skill: the mechanical brand check the pipeline calls."""

import re
import unicodedata
from urllib.parse import urlparse

from twitter_text import extract_urls_with_indices

from content_pipeline.models import BrandRules


def check_brand(text: str, rules: BrandRules) -> list[str]:
    """Report banned phrases, banned characters, hashtags and disallowed link domains."""
    errors = []
    # NFKC folds look-alikes (fullwidth ＃, ligatures) so they cannot slip past the checks.
    folded = unicodedata.normalize("NFKC", text)

    for word in rules.banned_words:
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", folded, re.I):
            errors.append("banned_phrase:" + word)

    for char in rules.banned_characters:
        if char in text or char in folded:
            errors.append("banned_character:" + char)

    if re.search(r"(?<!\w)#[\w]+", folded):
        errors.append("hashtag")

    for link in extract_urls_with_indices(folded):
        url = link["url"]
        domain = urlparse(url if "://" in url else "https://" + url).hostname

        if domain not in rules.allowed_links:
            errors.append("link_domain:" + str(domain))

    return errors
