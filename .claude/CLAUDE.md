# Working rules for this repository

## Don't upload anything

Do not publish, upload, or otherwise send this repository's content to an
external service. That includes Artifacts on claude.ai, gists, pastebins, and
any other hosted page — whether or not the destination is private by default.

This is an IETF service repository and its design documents, API contracts and
code stay on the machine they are worked on.

Deliverables belong in the repository or in the terminal:

- Design plans and rationale go in `plan.md`, which is where this project keeps
  them, or in a file under `docs/`.
- Anything shorter is just an answer in the terminal.

Ask before creating an external link, rather than offering one and then
undoing it.

# Coding conventions

## Comments
- Default to no comment. A comment earns its place by carrying *why* or
  *what for*: a constraint, deliberate deviation, gotcha, workaround, domain
  fact, or the purpose of a step or branch. The test is "does it say what
  this is for", not "could the code be read to imply it".
  `// validate we've got valid data` above an if/throw and
  `// one channel means greyscale` above `channels === 1` both stay.
- Never narrate syntax: don't restate the operation the next line visibly
  performs ("loop over users", "return early") or repeat names, types or
  signatures. Type hints are the signature documentation.
- Never narrate the change: no "previously", "replaced", "no longer",
  "used to", "as requested", "fixed", "THE FIX". A comment must read
  correctly to someone who never saw the diff. Change context goes in the
  commit message. A regression test may describe the failure mode it guards;
  it must not describe the fix as a diff.
- If a comment is inaccurate, correct it; don't delete it.
- Docstrings: a one-line summary on public functions/classes is fine. One
  that re-emits parameter names and types is narration. Keep what the
  signature can't show: units, ranges, side effects, failure modes,
  invariants.
- No pointers to moving targets ("see the design doc", "spec §7", audit
  finding labels like "Defect #3" or "G4"). Encode the substance; a link to
  the issue/PR or a stable-path README is fine as a trailing breadcrumb.
- Cite issues and PRs by full URL, never `org/repo#N` or bare `#N`.
  Vendor-neutral: the shorthand assumes one host.
- Refer to the variable name in comments, not the literal value it holds.
- TODO/FIXME are fine without issue IDs.
- When auditing comments, delete only: dead commented-out code, change
  narration, false statements, and bare dividers that repeat the identifier
  beneath them. When in doubt, keep.

## General
- Grep for an existing type/util/dependency before inventing one; ask before
  adding Error subclasses or concurrency primitives.
- Feature logic lives in its own module; UI components import and call it.
  Split into a directory if it grows.
- Single-use consts and helpers live inside the test that uses them, not at
  module scope.
- Fix code where it is; never override via CSS cascade/specificity or
  patching around the original.
- Don't add `autofocus` to inputs.
- Utility scripts in Node/TypeScript, never Python or perl.
- Never start or restart the dev server; the user manages it.
- Never publish anything (Artifacts, hosted pages); write a local file.

## TypeScript
- Prefer `const fn = () =>` over `function` declarations.
- Prefer `type` aliases over `interface`.
- Prefer destructuring (`const { x } = obj`) over repeated `obj.x`.
- Prefer template literals over `+` concatenation.
- Narrow with `typeof`/`instanceof` guards; `as` casts only as a last resort.
- Vue: only pass a getter/ref when the instance can outlive the value; never
  name a getter const as if it were a value.
- Lint/format with oxlint + oxfmt. Never suggest Prettier.
- Run tests via the project's npm scripts, never `npx vitest` directly.
