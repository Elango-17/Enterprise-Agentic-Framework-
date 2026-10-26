from typing import Any, Dict, List, Optional
import requests
from requests.auth import HTTPBasicAuth
from tools.base import BaseTool


class ExtractTestrailTestCases(BaseTool):
    """Tool to extract test cases from TestRail for a given project, suite, and section."""

    def execute(
        self,
        base_url: str,
        username: str,
        api_key: str,
        project_id: int,
        suite_id: Optional[int] = None,
        section_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Extracts test cases from TestRail.

        Args:
            base_url: The base URL of the TestRail instance.
            username: The username or email associated with the TestRail account.
            api_key: The API key or password for TestRail authentication.
            project_id: The ID of the project to extract test cases from.
            suite_id: The ID of the test suite (optional).
            section_id: The ID of the specific section to filter test cases (optional).

        Returns:
            A dictionary containing a list of extracted test cases.
        """
        if not base_url:
            raise ValueError("base_url is required.")
        if not username:
            raise ValueError("username is required.")
        if not api_key:
            raise ValueError("api_key is required.")
        if project_id is None:
            raise ValueError("project_id is required.")

        cleaned_base_url = base_url.rstrip("/")
        api_url = f"{cleaned_base_url}/index.php?/api/v2/get_cases/{project_id}"

        params: Dict[str, Any] = {}
        if suite_id is not None:
            params["suite_id"] = suite_id
        if section_id is not None:
            params["section_id"] = section_id

        auth = HTTPBasicAuth(username, api_key)
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.get(
                api_url,
                auth=auth,
                headers=headers,
                params=params,
                timeout=30,
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as http_err:
            raise RuntimeError(
                f"HTTP error occurred while connecting to TestRail: {http_err} - Response: {response.text}"
            )
        except requests.exceptions.RequestException as req_err:
            raise RuntimeError(
                f"Request error occurred while connecting to TestRail: {req_err}"
            )

        data = response.json()

        # TestRail API typically returns a pagination dictionary for get_cases if there are many cases,
        # or a direct list depending on the exact API version/endpoints, but standard v2 get_cases returns
        # either a list or an object containing 'cases' if paginated. Let's handle both safely.
        test_cases_raw: List[Any] = []
        if isinstance(data, list):
            test_cases_raw = data
        elif isinstance(data, dict):
            if "cases" in data and isinstance(data["cases"], list):
                test_cases_raw = data["cases"]
            else:
                # Fallback if the response is a single dictionary or custom format
                test_cases_raw = [data]

        formatted_cases = []
        for case in test_cases_raw:
            formatted_cases.append(
                {
                    "id": case.get("id"),
                    "title": case.get("title"),
                    "preconditions": case.get("custom_preconds"),
                    "steps": case.get("custom_steps") or case.get("steps"),
                    "expected_result": case.get("custom_expected")
                    or case.get("expected"),
                    "section_id": case.get("section_id"),
                    "suite_id": case.get("suite_id"),
                    "priority_id": case.get("priority_id"),
                    "type_id": case.get("type_id"),
                }
            )

        return {"test_cases": formatted_cases}
