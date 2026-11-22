# Contributing to token-completion

## Start a change

The GitHub default branch, `stable`, is what users get when they clone.
Start feature, fix and documentation branches from the current `development`
tip. Select **development** as the base when opening your pull request.

```sh
git fetch origin
git switch -c feat/my-change origin/development
```

Describe the user-visible behavior in the PR and include the checks you ran.
Update examples when command syntax or configuration changes. Add a regression
test when existing coverage would miss the change.

## Test from the checkout

Use zsh, Git, fzf and Python 3.11+ with PyYAML. With a sibling checkout of shell-overrides, enable the terminal integration test before running the suite.

```sh
export SHELL_OVERRIDES_TEST_PLUGIN="$(cd ../shell-overrides && pwd)/shell-overrides.plugin.zsh"
python3 -m unittest discover -s tests -v
```

For interactive checks, follow the [disposable shell instructions](README.md#development).

## Promote a release

Both channels are branches so ordinary Git checkouts can follow them with
`git pull --ff-only`. Each release also gets a permanent annotated version
tag. A consumer can pin the tag even after both channels advance.

1. Set the release version in `package.nix` through a PR to `development`.
2. Fetch the merged commit, record its SHA, and check it out detached. Run the
   suite above on that exact checkout. For changes spanning both plugins, run
   the integration test with both release candidates.
3. Confirm that the candidate descends from the current `stable`. Publish
   the tested SHA and its new tag together. If another promotion wins the
   race or the ancestry check fails, inspect the new history before retrying.

```sh
git fetch origin --tags
candidate=$(git rev-parse origin/development)
git switch --detach "$candidate"
# Run the tests above here. Continue only after they pass.
version=$(sed -n 's/^  version = "\([^"]*\)";$/\1/p' package.nix)
test -n "$version" &&
  git merge-base --is-ancestor origin/stable "$candidate" &&
  git tag -a "v$version" "$candidate" -m "Release $version" &&
  git push --atomic origin "${candidate}:refs/heads/stable" "refs/tags/v$version"
```

Keep existing version tags at their original commits. If a released version
needs an urgent fix while development is ahead, branch from `stable`, test
and publish a new patch version, then merge the released commit back into
`development` so the next promotion can fast-forward.
