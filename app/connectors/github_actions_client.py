"""GitHub Actions API Client — fully wired for block, rerun, optimize."""

import base64
import requests
import yaml


class GitHubActionsClient:
    def __init__(self, token: str):
        self.base_url = "https://api.github.com"
        self.token = token
        self.owner: str = ""
        self.repo: str = ""
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        }

    def block_deployment(self, run_id: str) -> dict:
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/actions/runs/{run_id}/cancel"
        resp = requests.post(url, headers=self.headers)
        return {"status_code": resp.status_code, "response": resp.text}

    def rerun_job(self, run_id: str) -> dict:
        url = f"{self.base_url}/repos/{self.owner}/{self.repo}/actions/runs/{run_id}/rerun"
        resp = requests.post(url, headers=self.headers)
        return {"status_code": resp.status_code, "response": resp.text}

    def get_workflow_content(self, owner: str, repo: str, workflow_path: str, ref: str = "main") -> tuple[str | None, str | None]:
        """Fetch workflow YAML and its SHA (needed for update)."""
        url = f"{self.base_url}/repos/{owner}/{repo}/contents/{workflow_path}?ref={ref}"
        resp = requests.get(url, headers=self.headers)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {resp.text}"
        content = resp.json()
        yaml_content = base64.b64decode(content["content"]).decode()
        sha = content["sha"]
        return yaml_content, sha

    def update_workflow_content(
        self,
        owner: str,
        repo: str,
        workflow_path: str,
        new_yaml: str,
        sha: str,
        commit_msg: str = "[auto-optimizer] Optimize pipeline",
    ) -> dict:
        """Push updated workflow YAML back to GitHub."""
        url = f"{self.base_url}/repos/{owner}/{repo}/contents/{workflow_path}"
        data = {
            "message": commit_msg,
            "content": base64.b64encode(new_yaml.encode()).decode(),
            "sha": sha,
        }
        resp = requests.put(url, headers=self.headers, json=data)
        return {"status_code": resp.status_code, "response": resp.text}

    def quarantine_test(self, owner: str, repo: str, workflow_path: str, test_job: str, ref: str = "main") -> dict:
        """Remove a flaky test job from the workflow YAML and push the change."""
        yaml_content, sha = self.get_workflow_content(owner, repo, workflow_path, ref)
        if not yaml_content:
            return {"error": sha}
        data = yaml.safe_load(yaml_content)
        jobs = data.get("jobs", {})
        if test_job in jobs:
            del jobs[test_job]
        data["jobs"] = jobs
        new_yaml = yaml.dump(data, default_flow_style=False)
        return self.update_workflow_content(
            owner, repo, workflow_path, new_yaml, sha,
            commit_msg=f"[auto-optimizer] Quarantine flaky job: {test_job}",
        )
