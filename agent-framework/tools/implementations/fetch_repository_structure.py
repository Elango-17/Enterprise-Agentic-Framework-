from typing import Any, Dict, Optional
import os
import base64
import requests
from urllib.parse import urlparse
from tools.base import BaseTool


class FetchRepositoryStructure(BaseTool):
    """Retrieves the directory and file structure of a given code repository."""

    def execute(
        self,
        repository_url: str,
        branch: Optional[str] = None,
        auth_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieves the directory and file structure of a given code repository.

        Args:
            repository_url: The URL of the repository to fetch the structure from.
            branch: The specific branch to fetch the structure for.
            auth_token: Personal access token for private repositories.

        Returns:
            A dictionary representing the hierarchical tree structure of the repository.
        """
        if not repository_url or not isinstance(repository_url, str):
            raise ValueError("A valid 'repository_url' string is required.")

        owner, repo = self._parse_github_url(repository_url)
        token = auth_token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        api_base = f"https://api.github.com/repos/{owner}/{repo}"

        # If branch is not specified, get the default branch
        target_branch = branch
        if not target_branch:
            try:
                repo_resp = requests.get(api_base, headers=headers, timeout=15)
                if repo_resp.status_code == 404:
                    raise ValueError(f"Repository not found or access denied: {owner}/{repo}")
                repo_resp.raise_for_status()
                target_branch = repo_resp.json().get("default_branch", "main")
            except requests.RequestException as e:
                raise RuntimeError(f"Failed to fetch repository metadata: {e}")

        # Fetch the recursive tree
        tree_url = f"{api_base}/git/trees/{target_branch}?recursive=1"
        try:
            tree_resp = requests.get(tree_url, headers=headers, timeout=30)
            if tree_resp.status_code == 404:
                raise ValueError(f"Branch '{target_branch}' not found or tree API not accessible.")
            tree_resp.raise_for_status()
            tree_data = tree_resp.json()
        except requests.RequestException as e:
            raise RuntimeError(f"Failed to fetch repository tree structure: {e}")

        items = tree_data.get("tree", [])
        hierarchical_tree = self._build_tree(items)

        return {
            "repository": f"{owner}/{repo}",
            "branch": target_branch,
            "truncated": tree_data.get("truncated", False),
            "structure": hierarchical_tree
        }

    def _parse_github_url(self, url: str) -> tuple[str, str]:
        """Extracts owner and repo name from a GitHub URL."""
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]
        
        if len(path_parts) < 2:
            raise ValueError(f"Invalid repository URL format: {url}. Expected owner/repo.")
        
        owner = path_parts[0]
        repo = path_parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
            
        return owner, repo

    def _build_tree(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Constructs a nested hierarchical tree from a flat list of git tree items."""
        root: list[dict[str, Any]] = []
        path_map: dict[str, dict[str, Any]] = {}

        # Sort items by path length to ensure parents are processed before children
        sorted_items = sorted(items, key=lambda x: len(x.get("path", "").split("/")))

        for item in sorted_items:
            path = item.get("path", "")
            item_type = item.get("type", "")  # 'blob' for file, 'tree' for directory
            sha = item.get("sha", "")
            size = item.get("size")

            parts = path.split("/")
            name = parts[-1]

            node = {
                "name": name,
                "path": path,
                "type": "directory" if item_type == "tree" else "file",
                "sha": sha
            }
            if size is not None and item_type == "blob":
                node["size"] = size

            if item_type == "tree":
                node["children"] = []

            path_map[path] = node

            if len(parts) == 1:
                root.append(node)
            else:
                parent_path = "/".join(parts[:-1])
                parent_node = path_map.get(parent_path)
                if parent_node and "children" in parent_node:
                    parent_node["children"].append(node)
                else:
                    # Fallback if parent wasn't found in map
                    root.append(node)

        return root
