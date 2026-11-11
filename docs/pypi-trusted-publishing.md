# PyPI Trusted Publishing with GitHub Actions OIDC

The project retains `semantic-release` for Conventional Commit versioning, Git tags and GitHub Releases. The `@semantic-release/exec` `prepareCmd` builds the version marker `.release-prepared`, updates the Python version files and allows the release workflow to build wheels and source distributions. PyPI publishing is moved into its own job with short-lived GitHub OIDC credentials and no static API token.

## Required one-time PyPI configuration **before merging this PR**

Use a maintainer's authenticated PyPI account:

- **Existing PyPI project**: https://pypi.org/ → Your projects → `hermes-ops-kit` → Manage → Publishing → Add a new publisher → GitHub Actions.
- **Unpublished PyPI project**: confirm ownership/availability of the exact name and use https://pypi.org/manage/account/publishing/ to create a *pending trusted publisher* for `hermes-ops-kit`. A GitHub Release alone does not establish that the PyPI project exists.

Configure exactly:

| PyPI field | Value |
|---|---|
| Package name | `hermes-ops-kit` |
| GitHub owner | `redoracle` |
| Repository | `hermes-ops-kit` |
| Workflow filename | `release.yml` |
| Environment | `release` |

The existing GitHub Actions workflow uses the **`release`** environment. The publication job reuses it: do not enter `pypi` or leave the environment blank in PyPI unless you also update this workflow. Review GitHub Settings → Environments → release; enforce required reviewers and appropriate deployment restrictions.

## How a release works

1. A legitimate release-worthy Conventional Commit reaches `main` (the current `.releaserc.json` supports feat/fix/perf/refactor).
2. `semantic-release` prepares the version and GitHub Release. The same job builds the versioned wheel and sdist, uploads the files to GitHub Releases, and stores the files in a short-lived GitHub Actions artifact. It then synchronizes version files on `main`.
3. Only if the release job **succeeds** and has generated `.release-prepared`, `pypi-publish` starts. It downloads exactly those built artifacts and uploads them using the SHA-pinned `pypa/gh-action-pypi-publish` action v1.14.3. The action uses GitHub OIDC to get temporary PyPI credentials.
4. Verify the new version, both distributions, and PyPI provenance attestations. **Rerunning a workflow without a release-worthy change does not validate OIDC**, because there will be no new version to publish.

## Cutover and credential retirement

- Register/verify the PyPI trusted publisher **before** merging this PR. Merge alone cannot create the required trust on PyPI.
- Trigger the **next genuine release** after PyPI trust is configured and GitHub approvals are satisfied. Compare the GitHub Release tag/version to the newly published PyPI version.
- Only **after successful OIDC publication** revoke the old token on PyPI and delete GitHub Actions secret `PYPI_API_TOKEN`, provided no other workflow depends on it. The new workflow contains neither `PYPI_API_TOKEN` nor `TWINE_PASSWORD`.
- Do not attempt to republish a version already on PyPI: versions are immutable. If a GitHub-only release exists, first reconcile package state and release version history.

## Security details

- The `release` job retains `contents: write` for GitHub Releases and version synchronization and has **no** `id-token: write` grant.
- The **separate** PyPI publishing job has `contents: read` and `id-token: write` and runs no checkout, build scripts or application code.
- `pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086` is v1.14.3 (the patch for GHSA-5xjx-3w67-w9x9) and supports PEP 740/Sigstore provenance attestations.
- An OIDC failure means the PyPI project/trusted-publisher identity, workflow filename, environment or ownership must be checked. Do not add a long-lived token to fix it.

References:
https://docs.pypi.org/trusted-publishers/adding-a-publisher/
https://docs.pypi.org/trusted-publishers/using-a-publisher/
https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/
