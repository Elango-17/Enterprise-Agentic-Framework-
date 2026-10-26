from typing import Any, Dict
import requests
from requests.auth import HTTPBasicAuth
from tools.base import BaseTool


class FetchJiraTicket(BaseTool):
    """Tool to retrieve details of a specific Jira ticket using its ticket key or ID."""

    def execute(
        self,
        ticket_key: str,
        jira_instance_url: str,
        api_token: str,
        username: str
    ) -> Dict[str, Any]:
        """
        Retrieves details of a specific Jira ticket using its ticket key or ID.

        Args:
            ticket_key: The unique key or ID of the Jira ticket (e.g., PROJ-123).
            jira_instance_url: The base URL of the Jira instance.
            api_token: The authentication API token or password for Jira access.
            username: The username or email associated with the Jira account.

        Returns:
            A dictionary containing the Jira ticket details.
        """
        if not ticket_key or not isinstance(ticket_key, str):
            raise ValueError("A valid 'ticket_key' string is required.")
        if not jira_instance_url or not isinstance(jira_instance_url, str):
            raise ValueError("A valid 'jira_instance_url' string is required.")
        if not api_token or not isinstance(api_token, str):
            raise ValueError("A valid 'api_token' string is required.")
        if not username or not isinstance(username, str):
            raise ValueError("A valid 'username' string is required.")

        base_url = jira_instance_url.rstrip("/")
        endpoint = f"{base_url}/rest/api/2/issue/{ticket_key.strip()}"

        auth = HTTPBasicAuth(username, api_token)
        headers = {
            "Accept": "application/json"
        }

        try:
            response = requests.get(
                endpoint,
                auth=auth,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as http_err:
            status_code = http_err.response.status_code if http_err.response else "Unknown"
            error_message = f"HTTP error occurred while fetching Jira ticket {ticket_key} (Status {status_code}): {http_err}"
            raise RuntimeError(error_message) from http_err
        except requests.exceptions.RequestException as req_err:
            error_message = f"Network or request error occurred while fetching Jira ticket {ticket_key}: {req_err}"
            raise RuntimeError(error_message) from req_err

        try:
            ticket_data = response.json()
        except ValueError as json_err:
            raise RuntimeError(f"Failed to parse JSON response from Jira instance: {json_err}") from json_err

        return ticket_data
