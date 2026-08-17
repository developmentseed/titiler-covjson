Dependabot's `uv` updates are lockfile-only and move only direct dependencies,
so transitive entries in `uv.lock` drift until the whole file is re-locked. A
stale transitive can also sit on an open security advisory that Dependabot will
not fix, because it has no update path to offer. Refreshing also keeps the
`highest` CI leg meaningful: that leg is meant to test the top of the dependency
range, which the lockfile stops representing as it ages.

## Steps

Preview what would change:

```bash
uv lock --upgrade --dry-run
```

Then, on a branch:

```bash
git switch -c chore/refresh-lockfile
uv lock --upgrade
uv sync --locked
uv run pytest
git add uv.lock
git commit -m "build(deps): refresh stale transitive lock entries"
git push -u origin chore/refresh-lockfile
gh pr create --fill
```

CI covers both resolutions and the Docker smoke test, so let it finish before
merging.

## When a package will not move

`uv lock --upgrade` resolves only within the bounds in `pyproject.toml`. If a
transitive entry stays behind a patched version, a parent's own constraint is
holding it: check the parent's requirement and bump the parent instead.

## Closing this issue

Close it once the refresh lands, or immediately if the dry run shows nothing
worth taking this quarter.
