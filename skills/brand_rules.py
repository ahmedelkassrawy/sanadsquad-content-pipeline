"""Brand-rules skill: the mechanical brand check the pipeline calls."""

import re
import unicodedata
from urllib.parse import urlparse, urlunparse

from twitter_text import extract_urls_with_indices

from content_pipeline.models import BrandRules, ResearchPack


def canonical_url(url: str) -> str:
    """Normalize the stable URL parts used to match published source links."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), host + port, path, "", parsed.query, ""))


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
        elif canonical_url(url) not in {canonical_url(item) for item in pack.fetched_urls}:
            errors.append("link_not_fetched:" + url)

    return errors


def check_research_separation(draft, pack, spec, *, enforce_marketing_web=True) -> list[str]:
    """Enforce source separation after writing, independent of the prompt."""
    cited = {source_id for tweet in draft.tweets for source_id in tweet.source_ids}
    usable_kb = {
        source.source_id for source in pack.sources if source.kind == "kb" and source.status == "ok"
    }
    usable_web = {
        source.source_id: source.url
        for source in pack.sources
        if source.kind == "web" and source.status == "ok" and source.url
    }
    if spec.name == "marketing-post":
        kb_ids = {source.source_id for source in pack.sources if source.kind == "kb"}
        if kb_ids and not pack.kb_allowed:
            return ["marketing_kb_forbidden"]
        # Report every missing source and citation together: naming only the first lets a
        # revision fix that one and drop the other.
        missing = []
        if pack.kb_allowed:
            if not usable_kb:
                missing.append("marketing_kb_source_required")
            elif not cited.intersection(usable_kb):
                missing.append("marketing_kb_citation_required")
        if enforce_marketing_web:
            if not usable_web:
                missing.append("marketing_web_source_required")
            elif not cited.intersection(usable_web):
                missing.append("marketing_web_citation_required")
        if missing:
            return missing
    if spec.name == "tech-thread":
        if cited.intersection(usable_kb):
            return []
        published = {
            canonical_url(link["url"])
            for tweet in draft.tweets
            for link in extract_urls_with_indices(tweet.text)
        }
        cited_web_urls = {
            canonical_url(url) for source_id, url in usable_web.items() if source_id in cited
        }
        if cited_web_urls.intersection(published):
            return []
        if cited_web_urls:
            return [f"thread_web_link_required:{url}" for url in sorted(cited_web_urls)]
        return ["thread_citation_required"]
    return []
