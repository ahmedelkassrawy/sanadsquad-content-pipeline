from content_pipeline.research import KnowledgeBase


def search_kb(kb, query):
    return kb.search(query, 5)
