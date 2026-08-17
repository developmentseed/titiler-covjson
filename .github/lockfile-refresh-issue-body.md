<!--
Soft-wrapped deliberately, unlike every other Markdown file here. This one
becomes a GitHub issue body (opened by the workflow in
.github/workflows/lockfile-refresh-reminder.yml), and GitHub renders issue and
comment bodies with hard line breaks, so wrapping at 80 columns would show up as
ragged text in the created issue. Keep one line per paragraph. GitHub hides this
comment when it renders the issue.

MD013 is the 80-column rule this file is exempt from; MD041 wants a leading H1,
but the issue title comes from `gh issue create --title`, so a heading here
would duplicate it.
-->
<!-- markdownlint-disable-file MD013 MD041 -->

Dependabot's `uv` updates are lockfile-only and move only direct dependencies, so transitive entries in `uv.lock` drift until the whole file is re-locked. A stale transitive can also sit on an open security advisory that Dependabot will not fix, because it has no update path to offer. Refreshing also keeps the `highest` CI leg meaningful: that leg is meant to test the top of the dependency range, which the lockfile stops representing as it ages.

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

CI covers both resolutions and the Docker smoke test, so let it finish before merging.

## When a package will not move

`uv lock --upgrade` resolves only within the bounds in `pyproject.toml`. If a transitive entry stays behind a patched version, a parent's own constraint is holding it: check the parent's requirement and bump the parent instead.

## Closing this issue

Close it once the refresh lands, or immediately if the dry run shows nothing worth taking this quarter.
