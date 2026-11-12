# Private Storypark deployment control

Copy the contents of this template directory into a separate PRIVATE GitHub
repository such as Marknel/storyparkScraper-deploy, with master as its default
branch. The application repository remains public and contains only hosted CI.
Register the main-pc runner only against this private control repository.

The workflow can be dispatched manually from master. Scheduled polling runs
every fifteen minutes only when the control repository's HOME_AUTO_DEPLOY
Actions variable is true. Keep it unset until a reviewed manual deployment has
succeeded.

The GitHub-hosted check job verifies that this control repository is private,
reads public Storypark master, and requires a successful completed CI push for
that exact commit and source repository. The home runner then checks out only
that verified application commit into a separate source directory and invokes
its guarded deployment script. Public pull-request code is never selected.

No public-repository workflow, repository_dispatch credential or source-repo
webhook has access to this home runner. The token has read-only contents
permission; public source metadata is read anonymously. If GitHub API
rate-limits a poll, it fails safely and can retry at the next interval.

Configure trusted owners and optional required reviewers for the home
environment in this private repository. Review changes to this workflow as
changes to the home server's deployment authority.

See the application repository's docs/home-deployment.md for the owner-operated
RDP installation command, runtime-account requirements and rollback behavior.
