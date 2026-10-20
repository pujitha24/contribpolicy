# Releasing contribpolicy

Releases go to PyPI from GitHub Actions with **Trusted Publishing**. PyPI trusts one workflow file
(`.github/workflows/release.yml`) in one repository (`pujitha24/contribpolicy`) running in one GitHub
environment (`pypi`). No PyPI API token exists anywhere, so there is no secret to store, rotate or leak.

Publishing a GitHub release starts the workflow. It runs:

1. **test**: the CI lint and test jobs (`ci.yml`, Python 3.10 to 3.13).
2. **build**: installs the hash-pinned build tools from `requirements-release.txt`, checks that the release
   tag equals `v` + `VERSION` from `src/contribpolicy/_meta.py`, runs `python -m build`, smoke-tests the
   wheel in a fresh venv, and uploads `dist/` as an artifact.
3. **publish**: runs in the `pypi` environment, gets a short-lived OIDC token, checks the metadata with
   twine and uploads the sdist and wheel to PyPI with attestations.

## One-time setup

Do these once, in this order. Steps 1 to 3 need you, in a browser; nobody else can do them for you.

### 1. PyPI account with two-factor authentication

1. Register at <https://pypi.org/account/register/> and confirm the email address.
2. Turn on two-factor authentication: **Account settings → Two factor authentication**. Add an
   authenticator app (TOTP) or a security key. PyPI requires 2FA before you can publish anything.
3. Save the recovery codes PyPI shows you somewhere safe, outside this repository.

### 2. Pending publisher on PyPI

The project does not exist on PyPI yet, so add a *pending* publisher. The first successful upload creates
the project and turns it into a normal trusted publisher.

1. Go to <https://pypi.org/manage/account/publishing/>.
2. Under **Add a new pending publisher**, choose the **GitHub** tab and enter exactly:

   | Field | Value |
   | --- | --- |
   | PyPI Project Name | `contribpolicy` |
   | Owner | `pujitha24` |
   | Repository name | `contribpolicy` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

3. Click **Add**.

A pending publisher does not reserve the name. Publish the first release soon after adding it. (On
2026-10-19, `contribpolicy` was still free on PyPI.)

### 3. `pypi` environment on GitHub

1. Open <https://github.com/pujitha24/contribpolicy/settings/environments> and click **New environment**.
2. Name it `pypi` (exactly, lower case) and click **Configure environment**.
3. Recommended:
   - **Required reviewers**: add yourself. Each publish then waits for you to click **Approve and deploy**,
     which gives you a last look before anything reaches PyPI.
   - **Deployment branches and tags**: choose **Selected branches and tags**, click **Add deployment branch
     or tag rule**, pick **Tag**, and enter `v*`. Only release tags can then deploy.
4. Save. The environment needs no secrets.

## Cutting a release

The example uses 0.1.1. Commits and tags are made on curie in `~/dev/oss-loop/contribpolicy`, where
signing is set up.

1. **Prepare the release commit** (already done for 0.1.1):
   - set `VERSION` in `src/contribpolicy/_meta.py` (the only place the version lives; `pyproject.toml`
     reads it) and the expected version in `tests/test_cli.py`;
   - add a section to `CHANGELOG.md` with the release date and the compare link at the bottom;
   - update the pinned version in the README's GitHub Actions example (`pipx install contribpolicy==X.Y.Z`)
     and in the "install from the Git tag" line.
2. **Check locally** (optional; the workflow repeats it):

   ```console
   pytest && ruff check . && ruff format --check .
   python -m build && twine check --strict dist/*
   ```

3. **Commit and push** to `main`, and wait for CI to pass.
4. **Tag** with a signed, annotated tag and push it:

   ```console
   git tag -s v0.1.1 -m "contribpolicy 0.1.1"
   git push origin v0.1.1
   ```

5. **Publish the GitHub release** from that tag, with the changelog section as notes:

   ```console
   gh release create v0.1.1 --verify-tag --title "contribpolicy 0.1.1" --notes-file notes.md
   ```

   or in the browser: **Releases → Draft a new release**, choose the existing tag `v0.1.1`, paste the
   changelog section, **Publish release**. A *draft* release does not start the workflow; publishing does.
6. **Approve** the `publish` job under **Actions → Release** if you set required reviewers.
7. **Verify**:

   ```console
   pipx install contribpolicy==0.1.1   # or: pipx upgrade contribpolicy
   contribpolicy --version             # contribpolicy 0.1.1
   ```

   The project page is <https://pypi.org/project/contribpolicy/>.
8. **After the first PyPI release**: remove the "Until the PyPI release is live, install from the Git tag"
   line from the README in the next commit.

## If something goes wrong

- **`invalid-publisher` from PyPI**: the pending publisher does not match the run. Compare the owner,
  repository, workflow file name (`release.yml`, not the workflow's display name) and environment (`pypi`)
  character by character.
- **"Release tag does not match the package version"**: the tag and `VERSION` differ. Delete the GitHub
  release and the tag, fix whichever is wrong, and tag again.
- **A test or build failure**: nothing was uploaded. Fix it on `main`, delete the release and the tag, and
  tag again.
- **A bad upload**: PyPI never accepts the same version twice, even after you delete it. Yank the bad version
  on PyPI (**Manage → Releases → Options → Yank**) and release the next patch version.
