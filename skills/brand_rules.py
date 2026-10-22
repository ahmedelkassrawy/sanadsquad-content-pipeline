"""Brand-rules skill: the mechanical brand check the pipeline calls."""

import re
import unicodedata
from urllib.parse import urlparse

from twitter_text import extract_urls_with_indices

from content_pipeline.models import BrandRules, ResearchPack


def check_brand(text: str, rules: BrandRules, pack: ResearchPack | None = None) -> list[str]:
    """Report banned phrases, banned characters, hashtags and unfetched links."""
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
        if pack is None:
            domain = urlparse(url if "://" in url else "https://" + url).hostname
            if domain not in rules.allowed_links:
                errors.append("link_domain:" + str(domain))
        elif url not in pack.fetched_urls:
            errors.append("link_not_fetched:" + url)

    return errors


def check_research_separation(draft, pack, spec) -> list[str]:
    """Enforce source separation after writing, independent of the prompt."""
    if spec.name == "marketing-post":
        kb_ids = {source.source_id for source in pack.sources if source.kind == "kb"}
        if kb_ids and not pack.kb_allowed:
            return ["marketing_kb_forbidden"]
        if (
            pack.kb_allowed
            and kb_ids
            and not any(kb_ids.intersection(tweet.source_ids) for tweet in draft.tweets)
        ):
            return ["marketing_kb_citation_required"]
    if spec.name == "tech-thread" and any(source.kind == "web" for source in pack.sources):
        urls = pack.fetched_urls
        published = {
            link["url"] for tweet in draft.tweets for link in extract_urls_with_indices(tweet.text)
        }
        return [f"thread_web_link_required:{url}" for url in urls if url not in published]
    return []
