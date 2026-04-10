from app.connectors.base import SourceMapper
from app.connectors.github_actions import GitHubActionsMapper
from app.connectors.gitlab_ci import GitLabCIMapper
from app.connectors.jenkins import JenkinsMapper


def get_mapper(source_system: str) -> SourceMapper:
    if source_system == "github_actions":
        return GitHubActionsMapper()
    if source_system == "gitlab_ci":
        return GitLabCIMapper()
    if source_system == "jenkins":
        return JenkinsMapper()
    raise ValueError(f"Unsupported source_system: {source_system}")
