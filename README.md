# Perchance key-mint test container

**Purpose:** answer one question with evidence — does Cloudflare Turnstile pass
for a real headless Chromium running on SnapDeploy's own IP (AWS Fargate),
and if a Perchance userKey is minted there, does it work server-side from
that same container?

**This is a TEST container. Never deploy it to the live `perchance-image-api`
container, and never push these files to that repo's `main` branch** (a push
to main redeploys the live API).

## How it works
- On startup (and on `POST /run`) it opens the Perchance generator page in
  headless Chromium, watches network traffic for `verifyUser`,
  `turnstileClientFail`, and generate requests, tries clicking Generate, and
  observes for up to ~3 minutes.
- If a userKey is ever observed in a browser request, it immediately tests
  that key server-side from the same container (real `POST
  .../api/generate`), and records whether an `imageId` came back.
- The result is served as JSON at `GET /`. **No key, cookie, or token is
  ever written to disk or to the result** — only booleans, counts, and (on
  success) the imageId.

## Deploy (parent agent, browser)
1. Create a NEW public GitHub repo named `perchance-keytest` and upload
   these files to its root: `Dockerfile`, `requirements.txt`,
   `test_app.py` (this README is optional).
2. In SnapDeploy, create a second container from that repo: Free Small
   (512 MB / 0.25 vCPU), port 8000, branch main. No environment variables.
   (Alternative: `POST /api/mobile/deploy` with a scoped token from the
   SnapDeploy API Keys page.)
3. Wait for the deploy, then open `GET https://<test-container-url>/` — the
   test runs automatically ~15 s after startup and takes ~4–5 min. Refresh
   until `"status": "done"`.
4. Read the result, then **delete the test container** (it burns
   container-hours while it exists; the live API is a separate container
   and is unaffected).

## Costs
1 deploy (of 10/day) + a few container-minutes (of 100 h/month).

## Interpreting the result
- `key_seen: false`, `verifyUser` in the hundreds, `turnstileFail > 0` →
  Turnstile does NOT pass on AWS IP. This matches the external evidence
  (Cloudflare managed challenges fail from datacenter IPs since July 2026;
  AWS EC2 explicitly fails) and closes the container path.
- `key_seen: true` + `server_side_test.outcome == "imageId"` → Perchance
  generation WORKS from SnapDeploy. The API could then run on SnapDeploy
  with a browser-minted key (key would need periodic re-minting; cf_clearance
  and keys are session-bound).
- `key_seen: true` + `server_side_test.outcome == "invalid_key"` → the
  browser minted a key but it does not work server-side even from the same
  IP (binding is stricter than IP alone).
- `browser_error` mentioning memory / OOM → Chromium does not fit in
  512 MB; the container path is untestable at the Free size.

## Known risks
- Chromium is heavy for 512 MB. Flags (`--single-process`,
  `--disable-dev-shm-usage`) are set to fit; if the container log shows
  OOM kills, that is itself the finding.
- Expected outcome (per external evidence) is Turnstile failure on the AWS
  IP. The test exists to confirm or deny that for Perchance specifically,
  not because success is likely.
