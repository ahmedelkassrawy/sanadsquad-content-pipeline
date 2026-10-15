from content_pipeline.research import WebSearch


def web_search(searcher, query):
    return [r.model_dump(mode="json") for r in searcher.search(query)]
