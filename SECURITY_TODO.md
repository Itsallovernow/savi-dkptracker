# ⚠️ OUTSTANDING SECURITY TASK — Rotate the leaked guild API key

**Status: NOT DONE. Do this from the home machine.**

## What happened

The real guild API key was committed to this repo in `dkp_client.ini` (initial
commit `e57929e`) and pushed to `origin/main`. The file has since been removed
from tracking and added to `.gitignore`, but **the key is still recoverable from
git history and is already on the remote.** It must be treated as compromised.

## What still needs to happen

- [ ] **Rotate the key.** Redeploy the backend with a new `ApiKeyValue`:
      ```bash
      cd backend
      sam deploy --guided   # set a fresh ApiKeyValue when prompted
      ```
- [ ] **Update local config.** Put the new key into your local `dkp_client.ini`
      (now gitignored — copy from `dkp_client.ini.example` if needed).
- [ ] **Redistribute** the new key to officers who run the client.
- [ ] **(Optional) Purge the old key from git history** with `git filter-repo`,
      then force-push. Note this rewrites history and does not help anyone who
      already cloned/forked — rotation above is the real fix.
      ```bash
      echo '<OLD_KEY>==>REDACTED' > /tmp/replace.txt
      git filter-repo --replace-text /tmp/replace.txt
      git push --force --all
      ```

## When complete

Delete this file and remove the `.kiro/hooks/security-rotation-reminder.kiro.hook`
hook so the reminder stops firing.
