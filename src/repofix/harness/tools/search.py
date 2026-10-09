from repofix.search import format_search_results

from .result import ToolResult


class SearchTools:
    def __init__(self, index):
        self.index = index

    def search(self, args):
        query, top_k = args.get("query"), args.get("top_k", 5)
        if (
            not isinstance(query, str)
            or not query.strip()
            or type(top_k) is not int
            or not 1 <= top_k <= 20
        ):
            raise ValueError("query must be nonempty and top_k must be in 1..20")
        return ToolResult(format_search_results(self.index.search(query, top_k=top_k)))
