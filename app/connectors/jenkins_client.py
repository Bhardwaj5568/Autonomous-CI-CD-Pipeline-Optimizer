"""Jenkins API Client — fully wired for block, rerun, optimize."""

import requests
from requests.auth import HTTPBasicAuth


class JenkinsClient:
    def __init__(self, base_url: str, username: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.auth = HTTPBasicAuth(username, api_token)

    def block_build(self, job_name: str, build_number: str) -> dict:
        url = f"{self.base_url}/job/{job_name}/{build_number}/stop"
        resp = requests.post(url, auth=self.auth)
        return {"status_code": resp.status_code, "response": resp.text}

    def rerun_build(self, job_name: str) -> dict:
        url = f"{self.base_url}/job/{job_name}/build"
        resp = requests.post(url, auth=self.auth)
        return {"status_code": resp.status_code, "response": resp.text}

    def get_job_config(self, job_name: str) -> tuple[str | None, str | None]:
        """Fetch Jenkins job config.xml."""
        url = f"{self.base_url}/job/{job_name}/config.xml"
        resp = requests.get(url, auth=self.auth)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {resp.text}"
        return resp.text, None

    def update_job_config(self, job_name: str, new_config_xml: str) -> dict:
        """Push updated config.xml back to Jenkins."""
        url = f"{self.base_url}/job/{job_name}/config.xml"
        resp = requests.post(
            url, auth=self.auth,
            data=new_config_xml,
            headers={"Content-Type": "application/xml"},
        )
        return {"status_code": resp.status_code, "response": resp.text}

    def quarantine_test(self, job_name: str, test_id: str) -> dict:
        return {"status": "not_implemented", "test_id": test_id}
