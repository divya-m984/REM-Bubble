# Release Checklist for v0.1.0

This checklist guides the repository owner through the final steps to tag and publish REM Bubbles v0.1.0.

## Pre-Release

- [ ] **Choose and set LICENSE**
  - The LICENSE file is currently empty and must be chosen before release
  - Options: MIT, GPL, Apache 2.0, or your preferred license
  - After choosing, add the full license text to the `LICENSE` file
  - Commit with message: `chore: set license to [LICENSE_NAME]`

- [ ] **Verify README and documentation**
  - Review README.md for accuracy and completeness
  - Check CONTRIBUTING.md, CHANGELOG.md, and SECURITY.md
  - Ensure all links are correct and no placeholders remain
  - Take a screenshot of REM Bubbles and add to README if desired (optional)

- [ ] **Run full test suite**
  ```bash
  .venv/bin/python -m unittest discover -s tests -v
  ```
  - All 760 tests must pass
  - No warnings or failures

- [ ] **Verify clean working tree**
  ```bash
  git status
  ```
  - No uncommitted changes
  - No untracked files (except `.venv`, build artifacts, etc.)

- [ ] **Confirm version consistency**
  ```bash
  .venv/bin/rem-bubbles --version
  .venv/bin/rem-bubbles doctor | head -1
  grep 'version = ' pyproject.toml
  grep '__version__' src/rem_bubbles/__init__.py
  ```
  - All should show `0.1.0`

## Tag & Push

- [ ] **Create an annotated Git tag**
  ```bash
  git tag -a v0.1.0 -m "REM Bubbles v0.1.0"
  ```
  - Or a lightweight tag if you prefer:
    ```bash
    git tag v0.1.0
    ```

- [ ] **Push main branch**
  ```bash
  git push origin main
  ```

- [ ] **Push the tag**
  ```bash
  git push origin v0.1.0
  ```

## GitHub Release

- [ ] **Create a GitHub Release**
  - Go to: https://github.com/divya-m984/REM-Bubble/releases
  - Click "Create a new release"
  - Select tag: `v0.1.0`
  - Title: `REM Bubbles v0.1.0`
  - Description: Use the contents of CHANGELOG.md section `[0.1.0]`
  - Click "Publish release"

## Post-Release

- [ ] **Verify release is live**
  - Visit: https://github.com/divya-m984/REM-Bubble/releases/tag/v0.1.0
  - Confirm tag and release are visible

- [ ] **Announce** (optional)
  - Consider announcing on relevant channels (Reddit, forums, social media, etc.)

---

## Notes

- Do not modify version numbers during this process (they are already 0.1.0)
- Do not create multiple tags for the same release
- If you need to make fixes after tagging, create v0.1.1 on a new tag
- The release checklist lives in `RELEASE_CHECKLIST.md` for future reference

## Questions?

Refer to CONTRIBUTING.md for development guidance, or consult GitHub's release documentation.

---

**Good luck with the release! 🚀**
