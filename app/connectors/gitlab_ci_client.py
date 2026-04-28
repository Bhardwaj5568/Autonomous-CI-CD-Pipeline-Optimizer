"""GitLab CI API Client — fully wired for block, rerun, optimize."""

import base64
import requests


class GitLabCIClient:
    def __init__(self, token: str, base_url: str = "https://gitlab.com/api/v4"):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.project_id: str = ""
        self.headers = {"PRIVATE-TOKEN": self.token}

    def block_pipeline(self, project_id: str, pipeline_id: str) -> dict:
        url = f"{self.base_url}/projects/{project_id}/pipelines/{pipeline_id}/cancel"
        resp = requests.post(url, headers=self.headers)
        return {"status_code": resp.status_code, "response": resp.text}

    def rerun_pipeline(self, project_id: str, pipeline_id: str) -> dict:
        url = f"{self.base_url}/projects/{project_id}/pipelines/{pipeline_id}/retry"
        resp = requests.post(url, headers=self.headers)
        return {"status_code": resp.status_code, "response": resp.text}

    def get_pipeline_config(self, project_id: str, file_path: str = ".gitlab-ci.yml", ref: str = "main") -> tuple[str | None, str | None]:
        """Fetch .gitlab-ci.yml content and last commit ID."""
        encoded_path = file_path.replace("/", "%2F")
        url = f"{self.base_url}/projects/{project_id}/repository/files/{encoded_path}?ref={ref}"
        resp = requests.get(url, headers=self.headers)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {resp.text}"
        content = resp.json()
        yaml_content = base64.b64decode(content["content"]).decode()
        return yaml_content, content.get("last_commit_id", "")

    def update_pipeline_config(
        self,
        project_id: str,
        file_path: str,
        new_yaml: str,
        last_commit_id: str,
        commit_msg: str = "[auto-optimizer] Optimize pipeline",
        branch: str = "main",
    ) -> dict:
        """Push updated .gitlab-ci.yml back to GitLab."""
        encoded_path = file_path.replace("/", "%2F")
        url = f"{self.base_url}/projects/{project_id}/repository/files/{encoded_path}"
        data = {
            "branch": branch,
            "content": new_yaml,
            "commit_message": commit_msg,
            "last_commit_id": last_commit_id,
        }
        resp = requests.put(url, headers=self.headers, json=data)
        return {"status_code": resp.status_code, "response": resp.text}

    def quarantine_test(self, project_id: str, test_id: str) -> dict:
        return {"status": "not_implemented", "test_id": test_id}
