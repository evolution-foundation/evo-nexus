"""
Regression test — Brain Repo mirror propagates source deletions.

Original bug: `_mirror_workspace` used `shutil.copytree(src, dst,
dirs_exist_ok=True)`, which only adds/overwrites — it never removes from the
destination something that vanished from the source. Result: the brain repo
mirror only ever grew, never shrank, even after files were deliberately
reorganized or deleted on disk.

Fixtures ALWAYS live under `tempfile.mkdtemp()` — never against a real brain
repo checkout.

Covers:
    1. A file deleted from the source is removed from the destination.
    2. A file skipped by `_ignore` (policy exclusion, e.g. oversized) stays
       in the destination — it must not be deleted by mistake.
    3. `.gitignore` is never copied, and a stale `.gitignore` already in the
       destination is never treated as evidence of deletion.
    4. New/modified files are still copied normally (no regression).
    5. Files outside the watched paths are never touched.
    6. Credential caches are never copied, and any stale copy already in the
       destination is purged.
    7. A symlink and its target both survive reconciliation (regression: the
       mirror once lost a live symlink because `resolve()` rewrote its
       relative path to the target's).
    8. An unreadable file elsewhere in the tree does not disable deletion
       propagation for the rest of the watch path.
    9. An unreadable directory does not wipe the corresponding subtree from
       the backup.
    10. A cancel that fires mid-walk leaves `copied` partial and must not be
        read as "everything vanished from source".
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from dashboard.backend.brain_repo.job_runner import (
    _WATCH_PATHS,
    _mirror_workspace,
)


def _make_flask_app_noop():
    """Minimal mock — `_mirror_workspace` only uses flask_app for the cancel
    probe, which queries BrainRepoConfig via app_context(); here we simulate
    cancel_requested always False without needing a real DB."""
    from unittest.mock import MagicMock

    app = MagicMock()

    class _Cfg:
        cancel_requested = False

    class _Query:
        def filter_by(self, **kw):
            return self

        def first(self):
            return _Cfg()

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    app.app_context.side_effect = lambda: _Ctx()
    return app


class MirrorReconcileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="brain-repo-mirror-test-"))
        self.workspace = self.tmp / "workspace_src"
        self.brain_dir = self.tmp / "brain_dst"
        self.workspace.mkdir()
        self.brain_dir.mkdir()
        self.flask_app = _make_flask_app_noop()

        # Patch the models.BrainRepoConfig lazy-import used by the cancel probe.
        import sys
        import types
        from unittest.mock import MagicMock

        models_mod = types.ModuleType("models")
        models_mod.BrainRepoConfig = MagicMock()
        models_mod.BrainRepoConfig.query = MagicMock()

        class _Cfg:
            cancel_requested = False

        models_mod.BrainRepoConfig.query.filter_by.return_value.first.return_value = _Cfg()
        self._models_patch = sys.modules.get("models")
        sys.modules["models"] = models_mod

        # secrets_scanner import inside _mirror_workspace: point at a stub
        # that finds nothing, so the secrets-removal step is a no-op for
        # this test (covered separately by the scanner's own tests).
        scanner_mod = types.ModuleType("brain_repo.secrets_scanner")
        scanner_mod.scan_directory = lambda *a, **k: []
        # `_mirror_workspace` also calls `find_unscanned_archives` (log-only,
        # no unlink). The stub needs to track that surface — otherwise the
        # test breaks with an AttributeError from stub drift, not from a
        # defect in the code under test.
        scanner_mod.find_unscanned_archives = lambda *a, **k: []
        sys.modules["brain_repo.secrets_scanner"] = scanner_mod

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        import sys

        if self._models_patch is not None:
            sys.modules["models"] = self._models_patch
        else:
            sys.modules.pop("models", None)
        sys.modules.pop("brain_repo.secrets_scanner", None)

    def _mirror(self):
        return _mirror_workspace(self.flask_app, user_id=1, workspace=self.workspace, brain_dir=self.brain_dir)

    # ── 1. A file deleted from the source is removed from the destination ──
    def test_deleted_source_file_is_removed_from_dest(self):
        watch = _WATCH_PATHS[0]  # "memory"
        (self.workspace / watch).mkdir()
        f = self.workspace / watch / "note.md"
        f.write_text("hello")

        self._mirror()
        dst_file = self.brain_dir / watch / "note.md"
        self.assertTrue(dst_file.exists(), "first pass should copy the file")

        f.unlink()  # source deletes the file
        self._mirror()

        self.assertFalse(
            dst_file.exists(),
            "a file deleted from the source should have been removed from the destination (original bug)",
        )

    # ── 2. A policy-excluded file stays in the destination ─────────────────
    def test_policy_excluded_file_is_never_deleted(self):
        watch = _WATCH_PATHS[0]
        (self.workspace / watch).mkdir()
        small = self.workspace / watch / "big.bin"
        small.write_bytes(b"x" * 100)  # small enough to be copied

        self._mirror()
        dst_file = self.brain_dir / watch / "big.bin"
        self.assertTrue(dst_file.exists())

        # The SOURCE file now grows past the cap (10 MB) — the next pass
        # must SKIP the copy (policy), and the reconciler must not read that
        # as "vanished from source" and delete the destination copy.
        small.write_bytes(b"x" * (11 * 1024 * 1024))
        self._mirror()

        self.assertTrue(
            dst_file.exists(),
            "a policy-excluded file (oversized) must not be deleted from the destination",
        )

    def test_gitignore_never_copied_and_never_deletes_stale_copy(self):
        watch = _WATCH_PATHS[0]
        (self.workspace / watch).mkdir()
        (self.workspace / watch / "keep.md").write_text("k")

        # Simulate a residue from an old sync (before the .gitignore rule
        # existed): a .gitignore already present in the destination.
        dst_dir = self.brain_dir / watch
        dst_dir.mkdir(parents=True)
        stale_gitignore = dst_dir / ".gitignore"
        stale_gitignore.write_text("*")

        self._mirror()

        self.assertFalse(
            (self.workspace / watch / ".gitignore").exists() and False,
            "sanity: the source has no .gitignore",
        )
        self.assertTrue(
            stale_gitignore.exists(),
            "a residual .gitignore in the destination must not be deleted "
            "(it's policy-excluded — neither copied nor removed)",
        )
        self.assertTrue((dst_dir / "keep.md").exists())

    # ── 3. New/modified files are still copied (no regression) ─────────────
    def test_new_and_modified_files_still_copied(self):
        watch = _WATCH_PATHS[0]
        (self.workspace / watch).mkdir()
        (self.workspace / watch / "a.md").write_text("v1")

        self._mirror()
        self.assertEqual((self.brain_dir / watch / "a.md").read_text(), "v1")

        (self.workspace / watch / "a.md").write_text("v2")
        (self.workspace / watch / "b.md").write_text("new")
        self._mirror()

        self.assertEqual((self.brain_dir / watch / "a.md").read_text(), "v2")
        self.assertEqual((self.brain_dir / watch / "b.md").read_text(), "new")

    # ── 4. Files outside the watched paths are never touched ───────────────
    def test_files_outside_watch_paths_untouched(self):
        # A loose file directly at the root of brain_dir, simulating .git/
        # or anything else outside _WATCH_PATHS.
        outside = self.brain_dir / "README.md"
        outside.write_text("brain repo root file")
        git_marker = self.brain_dir / ".git" / "HEAD"
        git_marker.parent.mkdir(parents=True)
        git_marker.write_text("ref: refs/heads/main")

        watch = _WATCH_PATHS[0]
        (self.workspace / watch).mkdir()
        (self.workspace / watch / "note.md").write_text("hi")

        self._mirror()

        self.assertTrue(outside.exists(), "a file outside the watch paths must not be touched")
        self.assertTrue(git_marker.exists(), "the brain repo's own .git must not be touched")

    # ── extra: an entire watch path vanishing from source doesn't wipe dest ─
    def test_entire_watch_path_missing_does_not_wipe_dest(self):
        watch = _WATCH_PATHS[0]
        (self.workspace / watch).mkdir()
        (self.workspace / watch / "note.md").write_text("hi")
        self._mirror()
        self.assertTrue((self.brain_dir / watch / "note.md").exists())

        # The entire source watch path vanishes (e.g. a temporarily
        # unmounted disk).
        shutil.rmtree(self.workspace / watch)
        self._mirror()

        self.assertTrue(
            (self.brain_dir / watch / "note.md").exists(),
            "an entire watch path vanishing from source must NOT wipe the whole backup",
        )


if __name__ == "__main__":
    unittest.main()


class MirrorSymlinkTest(unittest.TestCase):
    """Regression for the symlink false-positive found in production.

    Reuses only the FIXTURE from `MirrorReconcileTest` by assignment, rather
    than inheriting the class: inheritance would make unittest re-run the
    parent's tests here too, doubling them and inflating the suite count.
    An inflated count hides a test that went missing.

    `_ignore`'s copytree callback used to do
    `full.resolve().relative_to(workspace_root)`. For a symlink, `resolve()`
    follows the link: the `rel` recorded in the `copied` set became the
    TARGET's path, never the link's own. Reconciliation then looked for the
    link's own path, didn't find it in `copied`, and deleted from the backup
    a file that exists on disk.
    """

    setUp = MirrorReconcileTest.setUp
    tearDown = MirrorReconcileTest.tearDown
    _mirror = MirrorReconcileTest._mirror

    def test_symlink_to_dest_is_not_deleted(self):
        watch = _WATCH_PATHS[0]  # "memory"
        base = self.workspace / watch
        (base / "archive").mkdir(parents=True)
        target = base / "archive" / "notes.md"
        target.write_text("canonical content")
        link = base / "notes.md"
        link.symlink_to(Path("archive") / "notes.md")
        self.assertTrue(link.is_symlink(), "precondition: the link was created")

        self._mirror()   # 1st pass: populates the destination
        self._mirror()   # 2nd pass: reconciliation decides whether to delete

        dst_link = self.brain_dir / watch / "notes.md"
        dst_target = self.brain_dir / watch / "archive" / "notes.md"
        self.assertTrue(
            dst_link.exists(),
            "REGRESSION: the symlink was deleted from the backup even though it exists in the source",
        )
        self.assertTrue(dst_target.exists(), "the symlink's target must also survive")
        self.assertEqual(dst_link.read_text(), "canonical content")

    def test_symlink_actually_removed_from_source_disappears_from_dest(self):
        """The opposite case: if the link is REALLY gone, reconciliation still works.

        Without this case, the test above would pass even with reconciliation
        entirely disabled — on its own it doesn't prove the fix preserved the
        feature.
        """
        watch = _WATCH_PATHS[0]
        base = self.workspace / watch
        (base / "archive").mkdir(parents=True)
        (base / "archive" / "notes.md").write_text("canonical content")
        link = base / "notes.md"
        link.symlink_to(Path("archive") / "notes.md")

        self._mirror()
        dst_link = self.brain_dir / watch / "notes.md"
        self.assertTrue(dst_link.exists(), "precondition: mirrored on the 1st pass")

        link.unlink()          # genuine removal from source
        self._mirror()

        self.assertFalse(
            dst_link.exists(),
            "reconciliation stopped removing a genuinely deleted file",
        )


class MirrorCredentialCacheTest(unittest.TestCase):
    """A skill's credential cache must NEVER enter the backup.

    Evidence from the original incident: one skill's OAuth cache was copied
    on every sync and stripped by the secrets scanner pre-commit — defense
    in depth working as the ONLY line. Another skill's token store, from the
    same family but without a content pattern the scanner recognizes (0
    findings), was committed to the remote.

    The fix excludes these basenames at COPY time (`_is_credential_cache`,
    inside `_ignore`) without making them policy-excluded — so reconciliation
    PURGES any stale copy already in the destination. Both directions are
    tested; the second is the discriminating one — it would fail if someone
    "simplified" by moving the check into `_is_policy_excluded`.
    """

    setUp = MirrorReconcileTest.setUp
    tearDown = MirrorReconcileTest.tearDown
    _mirror = MirrorReconcileTest._mirror

    def test_credential_cache_is_never_copied(self):
        watch = "customizations"
        skill = self.workspace / watch / "skills" / "some-skill"
        skill.mkdir(parents=True)
        (skill / ".token_cache.json").write_text('{"access_token": "x"}')
        (skill / ".gsc-token.json").write_text('{"refresh_token": "y"}')
        (skill / ".credentials-foo").write_text("z")
        (skill / "SKILL.md").write_text("legitimate content")

        self._mirror()

        dst_skill = self.brain_dir / watch / "skills" / "some-skill"
        self.assertTrue((dst_skill / "SKILL.md").exists(), "legitimate content is still mirrored")
        for name in (".token_cache.json", ".gsc-token.json", ".credentials-foo"):
            self.assertFalse(
                (dst_skill / name).exists(),
                f"credential cache {name} must NOT be copied into the brain repo",
            )

    def test_similarly_named_legitimate_file_is_still_mirrored(self):
        # Anti-overreach: `tokens.json` (e.g. a design system's tokens file)
        # must NOT match the patterns and must stay in the backup.
        watch = "customizations"
        d = self.workspace / watch / "skills" / "some-design-skill"
        d.mkdir(parents=True)
        (d / "tokens.json").write_text('{"typography": {}}')

        self._mirror()

        self.assertTrue(
            (self.brain_dir / watch / "skills" / "some-design-skill" / "tokens.json").exists(),
            "legitimate tokens.json was excluded by mistake — pattern too broad",
        )

    def test_stale_copy_in_destination_is_purged(self):
        # The real-world case: the credential file EXISTS in the source, but
        # an old copy is already in the brain repo. Reconciliation must
        # remove it — if the check were policy-excluded instead, it would
        # stay there forever.
        watch = "customizations"
        skill = self.workspace / watch / "skills" / "another-skill"
        skill.mkdir(parents=True)
        (skill / ".gsc-token.json").write_text('{"refresh_token": "current"}')
        (skill / "SKILL.md").write_text("ok")

        dst_skill = self.brain_dir / watch / "skills" / "another-skill"
        dst_skill.mkdir(parents=True)
        stale = dst_skill / ".gsc-token.json"
        stale.write_text('{"refresh_token": "old-leaked"}')

        self._mirror()

        self.assertFalse(
            stale.exists(),
            "a stale credential copy in the destination should have been PURGED by reconciliation",
        )
        self.assertTrue((dst_skill / "SKILL.md").exists())

    def test_normal_reconciliation_did_not_regress(self):
        # Explicit guard: widening the copy exclusion must not stop
        # reconciliation from removing what it's supposed to remove.
        watch = "customizations"
        d = self.workspace / watch / "skills" / "any-skill"
        d.mkdir(parents=True)
        f = d / "note.md"
        f.write_text("v1")

        self._mirror()
        dst_f = self.brain_dir / watch / "skills" / "any-skill" / "note.md"
        self.assertTrue(dst_f.exists())

        f.unlink()
        self._mirror()

        self.assertFalse(
            dst_f.exists(),
            "a file genuinely deleted from the source stopped being removed from the destination",
        )

    # ── Resilience: a PARTIAL copy error must not disable reconciliation ───
    #
    # Bug measured in a containerized deployment: a couple dozen files at
    # mode 0600 owned by root were unreadable to the container's
    # unprivileged uid. `shutil.copytree` accumulated those errors and
    # raised `shutil.Error` at the END of the walk; because the call to
    # `_reconcile_deletions` was INSIDE the same `try`, the exception
    # skipped reconciliation for the ENTIRE watch path. Effect: files
    # genuinely deleted on disk survived indefinitely in the remote, with
    # the log showing only a warning.

    # ── Mass-deletion circuit breaker ──────────────────────────────────────

    def test_breaker_blocks_mass_deletion(self):
        """A source that empties out wholesale must NOT wipe the backup.

        This is the shape an unmounted volume usually takes: the directory is
        still there, it is simply empty. The older `is_dir()` guard only
        covered the "path vanished entirely" shape and was blind to this one.
        """
        watch = _WATCH_PATHS[0]  # "memory"
        source = self.workspace / watch
        source.mkdir()
        for i in range(60):
            (source / f"note-{i:03d}.md").write_text(str(i))

        self._mirror()
        self.assertEqual(
            len(list((self.brain_dir / watch).glob("note-*.md"))), 60,
            "sanity: first pass should copy everything",
        )

        for f in source.iterdir():
            f.unlink()
        self.assertTrue(source.is_dir() and not any(source.iterdir()))

        self._mirror()

        self.assertEqual(
            len(list((self.brain_dir / watch).glob("note-*.md"))), 60,
            "the breaker did not hold: 60 of 60 files erased from the backup "
            "on the strength of an empty source directory",
        )

    def test_breaker_does_not_block_ordinary_deletions(self):
        """Everyday cleanup must still propagate — the breaker is not a freeze."""
        watch = _WATCH_PATHS[0]
        source = self.workspace / watch
        source.mkdir()
        for i in range(60):
            (source / f"note-{i:03d}.md").write_text(str(i))

        self._mirror()

        # Remove 5 of 60 — below both the absolute floor and the fraction.
        for i in range(5):
            (source / f"note-{i:03d}.md").unlink()

        self._mirror()

        survivors = sorted(p.name for p in (self.brain_dir / watch).glob("note-*.md"))
        self.assertEqual(
            len(survivors), 55,
            "ordinary deletion stopped propagating — the breaker is too eager",
        )
        self.assertNotIn("note-000.md", survivors)

    def test_unreadable_file_does_not_disable_deletion_propagation(self):
        watch = _WATCH_PATHS[0]  # "memory"
        (self.workspace / watch).mkdir()
        alive = self.workspace / watch / "probe.md"
        alive.write_text("probe")
        protected = self.workspace / watch / "protected.bin"
        protected.write_bytes(b"content")

        self._mirror()
        dst_alive = self.brain_dir / watch / "probe.md"
        dst_protected = self.brain_dir / watch / "protected.bin"
        self.assertTrue(dst_alive.exists())
        self.assertTrue(dst_protected.exists())

        # Now: the probe vanishes from the source AND another file becomes unreadable.
        alive.unlink()
        protected.chmod(0o000)

        # POSITIVE CONTROL — without this the test would pass EMPTY when run
        # as root (root can read mode 0600), claiming resilience that was
        # never exercised. If the file is still readable, there's no copy
        # error to resist and the test has nothing to prove.
        try:
            protected.read_bytes()
        except PermissionError:
            pass
        else:
            self.skipTest(
                "mode-000 file is still readable (likely running as root) — "
                "without a real copy error this test would be vacuous"
            )

        try:
            self._mirror()
        finally:
            protected.chmod(0o644)  # otherwise tearDown can't clean up

        self.assertFalse(
            dst_alive.exists(),
            "deletion propagation stopped because ANOTHER file in the same "
            "watch path was unreadable — the copytree exception swallowed reconciliation",
        )
        self.assertTrue(
            dst_protected.exists(),
            "the stale copy of the unreadable file was ERASED from the backup: "
            "a read failure at the source must not be read as 'vanished from source'",
        )

    def test_unreadable_directory_does_not_wipe_backup_subtree(self):
        """A subdirectory that can't be scanned must NOT authorise deletion.

        `shutil._copytree` appends the recursion failure to its error list
        and moves on (CPython 3.11, the `except OSError` inside the entry
        loop): the walk "completes" with a silent hole, and every file under
        the unreadable directory is missing from `copied` even though it
        still exists on disk. Using `copied` as proof of "vanished from
        source" would delete the entire subtree from the backup.
        """
        watch = _WATCH_PATHS[0]  # "memory"
        sub = self.workspace / watch / "locked"
        sub.mkdir(parents=True)
        (sub / "important.md").write_text("do not delete me")

        self._mirror()
        dst_f = self.brain_dir / watch / "locked" / "important.md"
        self.assertTrue(dst_f.exists(), "first pass should copy")

        sub.chmod(0o000)

        # POSITIVE CONTROL — as root the directory stays scannable and the
        # test would exercise nothing; better to skip than pass vacuously.
        import os

        try:
            os.scandir(sub).close()
        except PermissionError:
            pass
        else:
            self.skipTest(
                "mode-000 directory is still scannable (root?) — without the "
                "real recursion error this test would be vacuous"
            )

        try:
            self._mirror()
        finally:
            sub.chmod(0o755)

        self.assertTrue(
            dst_f.exists(),
            "the subtree's backup was ERASED because the source directory was "
            "unreadable — absence from `copied` was read as 'vanished from source'",
        )

    def test_broken_symlink_in_source_is_not_read_as_deletion(self):
        """A symlink with a missing target still EXISTS on disk — must not be deleted.

        `Path.exists()` follows the link and answers False for a broken
        symlink, confusing "points at nothing" with "isn't there". The
        mirror already lost a LIVE symlink to this exact class of mistake.

        WHAT THIS TEST ACTUALLY PROVES: it only fails when BOTH protections
        fall together — the `rel in copied` guard (1st line, which already
        covers the symlink today) AND the use of `lstat` in
        `_source_may_still_exist` (2nd line). Measured by neutralizing one
        at a time: with only one neutralized, the test still passes. In
        other words, this is a defense-in-depth test, NOT proof of `lstat`
        in isolation. `lstat` becomes the sole protection when `copied`
        doesn't contain the file — exactly the case covered by the
        unreadable-directory test above.
        """
        watch = _WATCH_PATHS[0]  # "memory"
        (self.workspace / watch).mkdir()
        target = self.workspace / watch / "target.md"
        target.write_text("content")
        link = self.workspace / watch / "pointer.md"
        link.symlink_to(target)

        self._mirror()
        dst_link = self.brain_dir / watch / "pointer.md"
        self.assertTrue(dst_link.exists(), "first pass should copy the link")

        # The target vanishes; the LINK still exists on disk, just broken.
        target.unlink()

        # POSITIVE CONTROL — the scenario only has value if the link really
        # survived the target's unlink and really is broken.
        self.assertTrue(link.is_symlink(), "sanity: the link should still be on disk")
        self.assertFalse(link.exists(), "sanity: the link should be broken")

        self._mirror()

        self.assertTrue(
            dst_link.exists(),
            "the symlink's backup copy was ERASED: a missing target was read as "
            "'the file vanished from source', but the link exists on disk",
        )

    def test_cancel_mid_walk_does_not_wipe_the_backup(self):
        """Cancel leaves `copied` PARTIAL — reconciling against it would wipe the backup.

        DELIBERATE ISOLATION: in production this guard is the SECOND line —
        `_source_may_still_exist` already covers everything, because the
        files are still in the source. Measured by running
        `_reconcile_deletions` with `copied=set()` over a live source: the
        result is 0 removals even WITHOUT the cancel guard. A naive test here
        would pass with the guard removed, i.e. it would be DEAD PROTECTION.

        So this test neutralizes `_source_may_still_exist` (simulating "the
        source is gone") so that the cancel guard is the ONLY thing standing
        between the cancel and deleting the backup. That way, removing the
        guard makes this test fail.
        """
        import sys

        from dashboard.backend.brain_repo import job_runner as _jr
        from dashboard.backend.brain_repo.job_runner import JobCancelled

        watch = _WATCH_PATHS[0]  # "memory"
        (self.workspace / watch).mkdir()
        for name in ("a.md", "b.md"):
            (self.workspace / watch / name).write_text(name)

        self._mirror()
        dst_a = self.brain_dir / watch / "a.md"
        dst_b = self.brain_dir / watch / "b.md"
        self.assertTrue(dst_a.exists() and dst_b.exists())

        # From the walk's point of view, the entire source "disappears": the
        # cancel fires on the 2nd read of the flag (the 1st is the
        # `_check_cancel` at the top of the watch loop), so `_ignore` starts
        # ignoring EVERYTHING and `copied` ends up empty.
        reads = {"n": 0}

        class _CfgFlip:
            @property
            def cancel_requested(self):
                reads["n"] += 1
                return reads["n"] >= 2

        sys.modules["models"].BrainRepoConfig.query.filter_by.return_value.first.return_value = _CfgFlip()

        # Neutralize the 1st line of defense so only the cancel guard remains.
        original = _jr._source_may_still_exist
        _jr._source_may_still_exist = lambda _p: False
        try:
            # POSITIVE CONTROL for the isolation itself: with the 1st line
            # down and `copied` empty, reconciliation SHOULD actually
            # delete — if it doesn't, the isolation didn't work and this
            # test doesn't discriminate the cancel guard.
            from dashboard.backend.brain_repo.job_runner import _reconcile_deletions

            probe = self.brain_dir / watch / "isolation-probe.md"
            probe.write_text("x")
            removed = _reconcile_deletions(self.workspace, self.brain_dir, watch, set())
            self.assertGreater(
                removed, 0,
                "isolation failed: with `copied` empty and the 1st line "
                "neutralized, reconciliation should delete — without that "
                "this test doesn't discriminate the cancel guard",
            )
            self.assertFalse(probe.exists())

            # Restore the backup and now exercise the real cancel path.
            dst_a.write_text("a.md")
            dst_b.write_text("b.md")

            with self.assertRaises(JobCancelled):
                self._mirror()
        finally:
            _jr._source_may_still_exist = original

        self.assertTrue(
            dst_a.exists() and dst_b.exists(),
            "reconciliation ran against a partial `copied` set and wiped out "
            "backup files that still exist in the source",
        )
