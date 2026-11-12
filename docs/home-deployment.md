# Home deployment on main-pc

The live Windows deployment is C:\VMs\storyparkScraper, shared as
\\main-pc\VMs\storyparkScraper. It is separate from the development checkout.
On 12 November 2026 the deployed copy was at ef9490d with four uncommitted
source/test customizations for pause support and MQTT status, plus its own
config.yaml. Runtime sync was paused. The repository had no registered runner.

## First installation in RDP

Run PowerShell 7 as administrator on main-pc, under the existing scraper account.
From a reviewed staging checkout of this repository, run:

    pwsh -File .\scripts\install_home_runner.ps1

First create the private deployment-control repository using the template in
deployment/private-repo, with master as its default branch. The default name is
Marknel/storyparkScraper-deploy. Do not register the runner against the public
application repository.

The installer obtains the latest Windows x64 runner from the official
actions/runner release, requires its GitHub API SHA256 digest to match, and
installs it separately at C:\actions-runner-storypark. It prompts securely for
a GitHub PAT with Administration write for only the private deployment-control
repository. Use -DeploymentRepository owner/name if a different private name was
chosen. The installer verifies that the named repository is private before
requesting a registration token. This is different
from the ADO PAT. The persistent PAT is used only in memory to request a
short-lived runner-registration token; it is not passed to a process or saved.
GitHub's native runner setup prompts for the Windows service account password.

The default service identity is the existing scraper task identity when it can
be identified, otherwise the account running the installer. Specify
-WindowsAccount DOMAIN\user when the existing task identity needs clarification.
Keep the runner and scraper/profile under the same appropriate Windows account.
Changing accounts requires provisioning that account's credentials and login
session; copying another account's DPAPI files is not sufficient.

Confirm that main-pc-storypark is Online in the private repository's Actions runner
settings. The installer does not start a scrape, renew login, or unpause sync.

## Reviewed deployment

The private Deploy Storypark home workflow can be dispatched manually from its
master branch. Polling every fifteen minutes is disabled until the private
repository Actions variable HOME_AUTO_DEPLOY is explicitly set to true.

Before selecting the home runner, a GitHub-hosted job checks that its control
repository is private, resolves the current public Storypark master SHA, and
requires a completed successful CI push for that exact SHA and source
repository. It checks out only that verified commit into a separate source
directory. The deployment job targets the main-pc/storypark-deploy runner
labels. No dispatch token or webhook from the public application repository is
needed.

The source repository is public. A trusted-only deployment workflow in that
public repository would not isolate the runner: an external pull request can
propose another workflow with the same self-hosted labels. GitHub recommends
avoiding self-hosted runners on public repositories. The separate private
control repository keeps public pull-request execution away from the home host.
The public CI matrix continues on GitHub-hosted runners.

Limit control-repository write access to trusted owners. Optional required
reviewers on its home environment provide deployment approval; those approvals
are additional control, rather than isolation from public workflow code.

The equivalent manual deployment from a clean reviewed staging checkout is:

    pwsh -File .\scripts\deploy_home.ps1 -SourcePath C:\path\to\staging -TargetPath C:\VMs\storyparkScraper

The script requires a committed staging checkout. The first deployment verifies
the inspected ef9490d baseline and exact SHA256 hashes of the four known local
source customizations. Unexpected source edits or unmanaged file collisions
stop deployment for review. Later deployments check the previous deployment's
hash manifest before changing source.
Polling the same deployed revision verifies its managed source hashes and
returns without another copy or backup.

It acquires the live configuration's run.lock using the deployment's Python.
Finish an interactive login or archive run before deploying; the script does
not kill the scraper or its browser. It writes and verifies a protected source
backup under .deployment\backups, then copies only selected tracked application,
test, script, workflow and documentation files. Copy failures roll source back
from the verified backup.

The target's config.yaml, .env, archive, state, browser profiles, .git and .venv
are excluded from source copying. In particular, runtime.enabled and the
existing MQTT configuration remain the operator's configuration. Source file
deletions need separate review; there is no git reset, clean, stash or directory
purge in the deployment.

Dependencies are installed only if requirements changed or required imports are
missing. The CLI import/argument check and final source hashes must pass before
.deployment\current.json records success. Source rollback is automatic on
failure; dependency installation is not a transaction, so inspect package
changes separately if dependency installation failed.

After the first deployment, perform the documented login and a capped acceptance
sync under the scraper account. Confirm real story text/media and final status
before deliberately unpausing scheduled acquisition. Deployments never perform
these account or runtime actions automatically.

## Access boundary

From the development PC, SMB and RDP were available; SSH/WinRM were not, and
remote task/DCOM queries were unavailable. Therefore the runner installer needs
an owner-operated RDP session. Preparing these scripts does not install a runner
or change main-pc's schedule, runtime configuration or deployed source.

Relevant GitHub documentation:

- [Workflow security](https://docs.github.com/en/actions/reference/security/secure-use)
- [Fork workflow approval settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)
- [Runner registration API](https://docs.github.com/en/rest/actions/self-hosted-runners#create-a-registration-token-for-a-repository)
