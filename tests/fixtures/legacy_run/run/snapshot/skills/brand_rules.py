"""Brand-rules skill: the mechanical brand check the pipeline calls."""

import re
from urllib.parse import urlparse

from twitter_text import extract_urls_with_indices

from content_pipeline.models import BrandRules


def check_brand(text: str, rules: BrandRules) -> list[str]:
    """Report banned phrases, banned characters, hashtags and disallowed link domains."""
    errors = []

    for word in rules.banned_words:
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.I):
            errors.append("banned_phrase:" + word)

    for char in rules.banned_characters:
        if char in text:
            errors.append("banned_character:" + char)

    if re.search(r"(?<!\w)#[\w]+", text):
        errors.append("hashtag")

    for link in extract_urls_with_indices(text):
        url = link["url"]
        domain = urlparse(url if "://" in url else "https://" + url).hostname

        if domain not in rules.allowed_links:
            errors.append("link_domain:" + str(domain))

    return errors
