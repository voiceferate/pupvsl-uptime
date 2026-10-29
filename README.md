# PupVSL public uptime monitor

Independent HTTPS monitoring for public PupVSL endpoints. This repository contains
no Management application source, SSH/R2 credentials, database access or video IDs.
Canonical operator source is maintained in the private Management repository:
`scripts/external-uptime.py` and `deploy/external-monitor/`.

GitHub-hosted Ubuntu runners check Management login, player loader and the stable
channel every five minutes (minutes 03, 08, ... 58). Requests retry three times;
Telegram reports incident/recovery transitions and an explicit setup test. Only
sealed GitHub Actions Secrets contain the bot token and chat ID. No pull-request or
fork workflow can access them; the workflow runs only on schedule/manual dispatch.

`state.json` contains public probe status and notification state. Pending sends are
retried; a Telegram timeout can produce a duplicate, rather than losing an incident.
Healthy state is committed once daily to prevent public scheduled-workflow inactivity
expiry. Incident/recovery commits happen immediately. Latest per-run status is in Actions.
The monitor never calls the VPS to send an alert and can report a full VPS outage.

Standard public-repository GitHub runners are free; this uses no paid larger runners,
artifacts, Pages hosting or private-repository Actions minutes. Scheduled Actions can
be delayed/dropped by GitHub load; this is best-effort monitoring, without an SLA or
a guaranteed five-minute detection time. A GitHub-wide outage is also a blind spot.

Sources: [runner availability](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job),
[scheduled-workflow limits](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows).

To update: review/test the canonical script, then run Management's operator-only
`node deploy/external-monitor/install.mjs` using the existing Git credential and root
SSH access. Root seals the existing Telegram settings using GitHub's public key;
plaintext Telegram secrets do not leave the VPS for the operator's PC.
Monitor changes are separate from Management application deployment.
