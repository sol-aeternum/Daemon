"""@research subagent - parallel web search + synthesis."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from orchestrator.services.fetch.models import FetchResult
from orchestrator.services.fetch.service import FetchService
from orchestrator.subagents.base import BaseSubagent, SubagentResult, SubagentType


class ResearchSubagent(BaseSubagent):
    """Research subagent that performs parallel web searches and synthesizes results."""

    agent_type = SubagentType.RESEARCH
    description = (
        "Performs parallel web searches and synthesizes findings into a comprehensive report"
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize research subagent."""
        super().__init__(config)
        # Brave Search API key is read from Settings in
        # spawn.get_subagent_manager and threaded through `shared_config`.
        self.api_key = config.get("brave_api_key") if config else None
        self.base_url = "https://api.search.brave.com/res/v1/web/search"
        self.max_concurrent = 3  # Max parallel searches

    async def execute(self, task: str, context: dict[str, Any] | None = None) -> SubagentResult:
        """Execute research task with parallel searches.

        Strategy:
        1. Generate multiple search queries from the task
        2. Execute searches in parallel
        3. Synthesize results into a coherent report
        """
        if not self.api_key:
            return self._create_result(
                success=False,
                error="BRAVE_API_KEY not configured",
            )

        # Generate diverse search queries from the task
        queries = self._generate_queries(task)

        # Execute searches in parallel
        search_results = await self._parallel_search(queries)

        # Synthesize findings
        synthesis = self._synthesize(task, search_results)

        return self._create_result(
            success=True,
            data={
                "original_task": task,
                "queries_executed": queries,
                "search_results": search_results,
                "synthesis": synthesis,
            },
            metadata={
                "total_queries": len(queries),
                "successful_searches": sum(1 for r in search_results if "error" not in r),
            },
        )

    def _generate_queries(self, task: str) -> list[str]:
        """Generate multiple search queries from a research task.

        This creates 2-3 different query angles to get diverse perspectives.
        """
        task_lower = task.lower()
        queries = [task]  # Original query is always included

        # Add query variations based on task type
        if "news" in task_lower or "latest" in task_lower or "recent" in task_lower:
            queries.append(f"latest news {task}")
            queries.append(f"recent developments {task}")
        elif "how to" in task_lower or "tutorial" in task_lower or "guide" in task_lower:
            queries.append(f"tutorial {task}")
            queries.append(f"best practices {task}")
        elif "compare" in task_lower or "vs" in task_lower or "versus" in task_lower:
            queries.append(f"comparison {task}")
            queries.append(f"pros and cons {task}")
        elif "price" in task_lower or "cost" in task_lower or "buy" in task_lower:
            queries.append(f"price comparison {task}")
            queries.append(f"review {task}")
        else:
            # General research - add context and details queries
            queries.append(f"overview {task}")
            queries.append(f"detailed information {task}")

        return queries[: self.max_concurrent]

    async def _parallel_search(self, queries: list[str]) -> list[dict[str, Any]]:
        """Execute multiple searches in parallel."""
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def search_with_limit(query: str) -> dict[str, Any]:
            async with semaphore:
                return await self._search(query)

        tasks = [search_with_limit(q) for q in queries]
        return await asyncio.gather(*tasks)

    async def _search(self, query: str) -> dict[str, Any]:
        """Execute a single search query."""
        if not self.api_key:
            return {
                "query": query,
                "error": "BRAVE_API_KEY not configured",
                "results": [],
            }

        try:
            api_key: str = self.api_key  # type: ignore[assignment]
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.base_url,
                    headers={
                        "X-Subscription-Token": api_key,
                        "Accept": "application/json",
                    },
                    params={
                        "q": query,
                        "count": 5,
                        "offset": 0,
                        "safesearch": "moderate",
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                web_results = data.get("web", {}).get("results", [])
                results = []
                urls_to_fetch = []
                for result in web_results[:5]:
                    result_data = {
                        "title": result.get("title", "No title"),
                        "url": result.get("url", ""),
                        "description": result.get("description", "")[:300],
                    }
                    results.append(result_data)
                    if result_data["url"]:
                        urls_to_fetch.append(result_data["url"])

                # Fetch full content for top results
                fetch_service = FetchService()
                fetch_tasks = [fetch_service.fetch(url) for url in urls_to_fetch[:5]]
                fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

                # Add full content to results
                for i, fetch_result in enumerate(fetch_results):
                    if i < len(results):
                        # Check if fetch was successful and not an exception
                        if not isinstance(fetch_result, Exception) and fetch_result is not None:
                            # Type check to satisfy LSP - fetch_result is a FetchResult here
                            if isinstance(fetch_result, FetchResult):
                                results[i]["full_content"] = fetch_result.content
                            else:
                                results[i]["full_content"] = None
                        else:
                            # Fallback to snippet if fetch failed
                            results[i]["full_content"] = None

                return {
                    "query": query,
                    "results": results,
                    "total_found": len(results),
                }

        except httpx.HTTPStatusError as e:
            return {
                "query": query,
                "error": f"Search API error: {e.response.status_code}",
                "results": [],
            }
        except Exception as e:
            return {
                "query": query,
                "error": f"Search failed: {str(e)}",
                "results": [],
            }

    def _synthesize(self, task: str, search_results: list[dict[str, Any]]) -> str:
        """Synthesize search results into a coherent report.

        This is a lightweight synthesis that can be enhanced with LLM in the future.
        For now, it creates a structured summary from the raw results.
        """
        successful_searches = [r for r in search_results if "error" not in r]

        if not successful_searches:
            return "No search results available."

        # Collect all unique sources
        all_sources = []
        seen_urls = set()
        for search in successful_searches:
            for result in search.get("results", []):
                url = result.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_sources.append(result)

        # Build synthesis
        lines = [
            f"# Research Report: {task}",
            "",
            f"**Search Strategy:** Executed {len(successful_searches)} parallel queries",
            f"**Sources Found:** {len(all_sources)} unique sources",
            "",
            "## Key Findings",
            "",
        ]

        # Add top findings from each search with full content if available
        for search in successful_searches[:3]:
            query = search.get("query", "")
            results = search.get("results", [])
            if results:
                top_result = results[0]
                lines.append(f'### From query: "{query}"')
                lines.append(f"- **{top_result.get('title', 'Untitled')}**")

                # Use full content if available, otherwise fallback to description
                full_content = top_result.get("full_content")
                if full_content:
                    # Truncate full content to reasonable length for synthesis
                    content_preview = (
                        full_content[:500] + "..." if len(full_content) > 500 else full_content
                    )
                    lines.append(f"  {content_preview}")
                else:
                    lines.append(f"  {top_result.get('description', 'No description')[:200]}...")

                lines.append(f"  Source: {top_result.get('url', 'Unknown')}")
                lines.append("")

        # Add all sources section
        lines.extend(
            [
                "## All Sources",
                "",
            ]
        )

        for i, source in enumerate(all_sources[:10], 1):
            lines.append(f"{i}. [{source.get('title', 'Untitled')}]({source.get('url', '')})")

        if len(all_sources) > 10:
            lines.append(f"\n... and {len(all_sources) - 10} more sources")

        return "\n".join(lines)
