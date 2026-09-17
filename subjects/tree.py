# Copyright The IETF Trust 2026, All Rights Reserved
"""Roll-up over the subject tree, in one place because four callers need it.

A subject covers the documents assigned to it and to everything beneath it.
Assigning smtp puts that document under email and under messaging too, which is
what makes a branch with no assignments of its own worth having: without it,
messaging is a heading that matches nothing, a subscription to it is dead the day
it is made, and its page in Red says there is nothing on the subject of messaging.

Four callers need the same answer -- subscription matching, subscriber counts,
survey audiences and the list serializer -- and if each ran its own join they
would drift apart one at a time. So they all come through here.

Nothing in this module recurses in SQL. Ancestors are the prefixes of a path, so
they are read off the string; descendants are one indexed prefix match. That
holds at any depth, and the four-level ceiling is not what makes it work.
"""

from collections import defaultdict

from .models import Subject, SubjectAssignment, ancestor_paths

# Re-exported so that a caller doing roll-up has one module to import from, even
# though the definition lives beside the path column it reads.
__all__ = [
    "ancestor_paths",
    "covering_subject_ids",
    "documents_under",
    "rollup",
    "subject_tree",
    "tree_ancestors",
    "tree_descendants",
]


def covering_subject_ids(docs):
    """The ids of every subject that covers any of these documents.

    Covering means assigned-or-above: a subject with one of the documents on it,
    and every subject that one sits under. This is what a subscription match tests
    against, and it is two queries whatever the shape of the tree -- the paths of
    the subjects holding the documents, and then the ids of those paths' prefixes.

    all_objects, because a retired subject goes on matching for the readers who
    already follow it. That is the whole point of retiring rather than deleting,
    and it is the rule subscriptions/matching.py already relies on by reaching
    Subscription.subject through the base manager.
    """
    assigned = set(
        Subject.all_objects.filter(assignments__doc__in=docs).values_list(
            "path", flat=True
        )
    )
    if not assigned:
        return set()
    wanted = set(assigned)
    for path in assigned:
        wanted.update(ancestor_paths(path))
    return set(Subject.all_objects.filter(path__in=wanted).values_list("pk", flat=True))


def documents_under(subject):
    """Every document the subject covers, deduplicated, in identifier order.

    One query over the subtree. A document assigned to two subjects in the same
    branch -- to smtp and to email both -- counts once.
    """
    docs = set(
        SubjectAssignment.objects.filter(
            subject__in=Subject.all_objects.at_or_under(subject)
        ).values_list("doc", flat=True)
    )
    return sorted(docs)


def rollup():
    """The whole vocabulary's assignments and coverage, in one pass.

    What the precomputer needs and what a per-subject query would make quadratic:
    six hundred subtree queries before it started on the metadata. Two queries
    instead, and then every subject's direct documents, covered documents and both
    counts fall out of adding each assignment to its own subject and to each of its
    ancestors.

    Returns (direct, covered), both keyed by subject path. Sorted lists rather
    than sets, because the caller publishes them and a precomputed file has to be
    byte-stable between runs that found the same data.
    """
    paths = dict(Subject.all_objects.values_list("pk", "path"))
    direct = defaultdict(set)
    covered = defaultdict(set)
    for path in paths.values():
        direct[path], covered[path] = set(), set()

    pairs = SubjectAssignment.objects.values_list("subject_id", "doc")
    for subject_id, doc in pairs:
        path = paths.get(subject_id)
        if path is None:
            continue
        direct[path].add(doc)
        covered[path].add(doc)
        for ancestor in ancestor_paths(path):
            covered[ancestor].add(doc)

    order = _doc_sort_key
    return (
        {path: sorted(docs, key=order) for path, docs in direct.items()},
        {path: sorted(docs, key=order) for path, docs in covered.items()},
    )


def subject_tree(direct, covered):
    """Every live subject's own entry, keyed by slug, with parent/children pointers.

    One query for the rows -- rollup() already cost the other two, for direct and
    covered -- built once per precompute run and then walked in memory rather than
    queried again: a subject's own file needs every ancestor up to the root and
    every descendant through its whole branch, and a query per ancestor and a
    query for the subtree is exactly the per-subject cost rollup() exists to
    spare the vocabulary-wide callers. tree_ancestors() and tree_descendants()
    below are how a caller snaps a slice of this off for one subject.
    """
    rows = list(Subject.objects.order_by("path"))
    children = {}
    for subject in rows:
        ancestors = subject.ancestor_slugs
        if ancestors:
            children.setdefault(ancestors[-1], []).append(subject.slug)

    tree = {}
    for subject in rows:
        ancestors = subject.ancestor_slugs
        tree[subject.slug] = {
            "id": subject.pk,
            "name": subject.name,
            "description": subject.description,
            "parent": ancestors[-1] if ancestors else None,
            "path": subject.path,
            "children": children.get(subject.slug, []),
            "documents": direct.get(subject.path, []),
            "document_count": len(direct.get(subject.path, [])),
            "document_count_deep": len(covered.get(subject.path, [])),
        }
    return tree


def tree_ancestors(tree, slug):
    """Every ancestor of slug in tree, root first, by following `parent` up."""
    ancestors = []
    parent = tree[slug]["parent"]
    while parent is not None:
        ancestors.append(parent)
        parent = tree[parent]["parent"]
    return list(reversed(ancestors))


def tree_descendants(tree, slug):
    """Every descendant of slug in tree -- its whole branch, not just direct
    children -- by following `children` down. Not siblings, and not slug itself."""
    descendants = []
    stack = list(tree[slug]["children"])
    while stack:
        child = stack.pop()
        descendants.append(child)
        stack.extend(tree[child]["children"])
    return descendants


def _doc_sort_key(doc):
    """Series first, then number, so rfc9 sorts before rfc10.

    Lexical order would put rfc10 before rfc9, which is not wrong so much as
    obviously unconsidered in a published file a person reads.
    """
    for index, char in enumerate(doc):
        if char.isdigit():
            return (doc[:index], int(doc[index:]))
    return (doc, 0)
