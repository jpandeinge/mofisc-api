# Security policy

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in a public issue, discussion, or
pull request. Use GitHub's private **Report a vulnerability** flow when it is
available. Otherwise, contact the repository owner privately through the
contact information on their GitHub profile.

Include the affected version or commit, reproduction steps, likely impact, and
any suggested mitigation. Do not include real customer or financial data.

## Supported code

Security fixes target the current default branch. Pre-release branches and old
commits are not supported releases.

## Sensitive data

Never commit environment files, service-account credentials, access tokens,
private keys, bank statements, customer exports, logs containing financial
records, trained private model artifacts, or unredacted screenshots. Revoke
and rotate any credential that may have been exposed; removing it from the
latest commit is not sufficient.
