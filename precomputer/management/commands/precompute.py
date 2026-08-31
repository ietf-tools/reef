# Copyright The IETF Trust 2026, All Rights Reserved
"""The precomputer's one entry point.

Red's precomputer has five scripts -- all, single, multiple, cron and publish
-- which differ in what they select and in how they report. Both of those are
arguments, not programs, so this is one command:

    manage.py precompute                      every task (the cron job)
    manage.py precompute stats subjects       named tasks
    manage.py precompute --doc rfc9110        one document's per-document files
    manage.py precompute --callback-url URL   report to a waiting caller

Exit status is 0 only if every selected task produced every file it meant to.
"""

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management.base import BaseCommand, CommandError

from precomputer.blobstore import get_blob_store
from precomputer.registry import TASKS
from reef import rfcmeta
from reef.docids import normalize_doc_id


class Command(BaseCommand):
    help = "Precompute the public API responses and upload them to the blob store."

    def add_arguments(self, parser):
        parser.add_argument(
            "tasks",
            nargs="*",
            metavar="TASK",
            help=f"Tasks to run. Defaults to all of: {', '.join(TASKS)}.",
        )
        parser.add_argument(
            "--doc",
            action="append",
            dest="docs",
            metavar="DOC",
            help=(
                "Restrict per-document tasks to this document, repeatable. "
                "Whole-series files are still rebuilt in full, since they "
                "cover this document too. Disables the purge."
            ),
        )
        parser.add_argument(
            "--concurrency",
            type=int,
            default=settings.REEF_PRECOMPUTE_CONCURRENCY,
            help="Parallel uploads (default %(default)s).",
        )
        parser.add_argument(
            "--no-purge",
            action="store_false",
            dest="purge",
            help="Keep keys the run no longer produces, instead of deleting them.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Render everything and report, without writing or deleting.",
        )
        parser.add_argument(
            "--no-metadata",
            action="store_false",
            dest="metadata",
            help=(
                "Skip resolving document titles from Red, writing null metadata "
                "instead. For working offline; the files stay the right shape."
            ),
        )
        parser.add_argument(
            "--callback-url",
            help=(
                "POST a JSON {type, message} result here when the run finishes, "
                "for a caller that triggered it and is waiting."
            ),
        )

    def handle(self, *args, **options):
        self.callback_url = options["callback_url"]
        try:
            message = self._run(options)
        except Exception as exc:
            self._report("error", f"{type(exc).__name__}: {exc}")
            raise
        self._report("success", message)
        self.stdout.write(self.style.SUCCESS(message))

    def _run(self, options):
        selected = self._select_tasks(options["tasks"])
        docs = self._parse_docs(options["docs"])
        dry_run = options["dry_run"]

        try:
            store = get_blob_store()
        except ImproperlyConfigured as exc:
            # A misconfigured store is the operator's mistake, not a crash, so
            # it reads as one line rather than a traceback in a cron log.
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Precomputing {', '.join(selected)} to {store}"
            + (" (dry run)" if dry_run else "")
        )

        # Once per run, not per lookup: validating roughly ten thousand entries is a
        # couple of seconds, and every task shares the one index.
        index = None
        if options["metadata"]:
            index = rfcmeta.load_index()
            if index is None:
                # Red being unreachable is not a reason to publish nothing. The files
                # are written with null metadata and the warning above says why.
                self.stderr.write(
                    self.style.WARNING(
                        "Could not load Red's index; writing null document metadata."
                    )
                )

        started = time.monotonic()
        written = set()
        clean_tasks = []
        failures = []

        for name in selected:
            func = TASKS[name]
            try:
                keys = self._run_task(
                    func, docs, index, store, options["concurrency"], dry_run
                )
            except Exception as exc:
                # One task's failure does not cancel the rest: the point of a
                # run is to refresh as much as it can, and a task that failed
                # leaves the previous payload in place rather than a gap.
                self.stderr.write(self.style.ERROR(f"[{name}] failed: {exc}"))
                failures.append(f"{name} ({type(exc).__name__}: {exc})")
                continue
            written |= keys
            clean_tasks.append(func)
            self.stdout.write(f"[{name}] {len(keys)} file(s)")

        purged = 0
        if options["purge"] and docs is None and not failures:
            purged = self._purge(store, clean_tasks, written, dry_run)
        elif options["purge"]:
            self.stdout.write(
                "Skipping purge: "
                + (
                    "--doc means the per-document keys were not all rebuilt."
                    if docs is not None
                    else "a task failed, so a missing key may just be one that "
                    "did not get rebuilt."
                )
            )

        if index is not None:
            # After the tasks, so that the unresolved list covers everything the run
            # actually asked for.
            index.report()

        elapsed = time.monotonic() - started
        summary = (
            f"{len(written)} file(s) and {purged} purge(s) planned, in {elapsed:.1f}s"
            if dry_run
            else f"{len(written)} file(s) written, {purged} purged, in {elapsed:.1f}s"
        )
        if failures:
            raise CommandError(f"Failed: {'; '.join(failures)}. {summary}.")
        return summary

    def _run_task(self, func, docs, index, store, concurrency, dry_run):
        """Render a task's files and upload them, returning the keys written.

        Rendering runs here, one at a time, because it is database work on this
        thread's connection. Uploading is network waiting, so it fans out.
        """
        keys = set()
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            uploads = []
            for key, body in func(docs=docs, index=index):
                keys.add(key)
                if dry_run:
                    self.stdout.write(f"  would write {key} ({len(body)} bytes)")
                    continue
                uploads.append(pool.submit(store.put, key, body))
            for upload in uploads:
                upload.result()  # re-raises, failing the task
        return keys

    def _purge(self, store, tasks, written, dry_run):
        """Delete keys a completed task owns but no longer produces.

        Only keys owned by a task that just ran are considered, so anything
        else sharing the bucket is left alone.
        """
        stale = [
            key
            for key in store.list_keys()
            if key not in written and any(func.owns.match(key) for func in tasks)
        ]
        for key in sorted(stale):
            if dry_run:
                self.stdout.write(f"  would purge {key}")
            else:
                store.delete(key)
                self.stdout.write(f"  purged {key}")
        return len(stale)

    def _select_tasks(self, names):
        if not names:
            return list(TASKS)
        unknown = [name for name in names if name not in TASKS]
        if unknown:
            raise CommandError(
                f"Unknown task(s): {', '.join(unknown)}. Available: {', '.join(TASKS)}."
            )
        return list(dict.fromkeys(names))

    @staticmethod
    def _parse_docs(raw):
        if not raw:
            return None
        try:
            return {normalize_doc_id(doc) for doc in raw}
        except DjangoValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc

    def _report(self, result_type, message):
        """POST the outcome to --callback-url, if one was given.

        A callback that cannot be reached is reported but does not change the
        run's own exit status: the files are either uploaded or they are not,
        and that is what the status has to mean.
        """
        if not self.callback_url:
            return
        body = json.dumps({"type": result_type, "message": message}).encode()
        request = urllib.request.Request(
            self.callback_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.stderr.write(self.style.ERROR(f"Callback POST failed: {exc}"))
