# V1.4 CI Audit Branch Marker

This branch is an audit-only branch created from `ce8d570` (V1.4 HEAD).
It is NOT a release branch; no production or Release branch is modified.

A push to `feature/**` triggers the repository's existing `ci.yml`
(build-and-test + non-blocking real Binance market probe), which consumes
no repository secrets. The full signoff report is committed next to
`reports/V14_GITHUB_CI_SIGNOFF.md` on this branch.
