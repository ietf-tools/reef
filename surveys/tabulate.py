# Copyright The IETF Trust 2026, All Rights Reserved
"""Survey responses as a human-readable table: one row per response, one column
per answer, with choice values replaced by the labels the respondent saw.

A response's data is SurveyJS's raw value object, keyed by question name and
holding choice values rather than their labels. Reading it takes the survey's
definition, so this walks the definition to decide the columns and how each
answer is shown.

Most answers are a single piece of text. Some are structured: a matrix answers
several rows at once, and a dynamic panel or dynamic matrix repeats a group of
questions as many times as the respondent chose. Those come out as a Cell tree
(Fields for labelled parts, Entries for repeats), which the HTML table nests and
the CSV flattens into lines within one cell.
"""

import csv
import re
from dataclasses import dataclass

from django.db.models import CharField, F, Func
from django.utils.html import format_html, format_html_join

from .models import Response


@dataclass(frozen=True)
class Fields:
    """Labelled parts of one answer, such as a matrix's rows."""

    items: tuple[tuple[str, "Cell"], ...]


@dataclass(frozen=True)
class Entries:
    """Repeats of one answer, such as a dynamic panel's panels."""

    items: tuple["Cell", ...]


Cell = str | Fields | Entries


@dataclass(frozen=True)
class Column:
    # A data key, or UNSTRUCTURED_KEY.
    key: str | object
    title: str
    # None for a key found in responses that the definition no longer declares.
    question: dict | None = None
    is_comment: bool = False


# Element types that are layout or display only and never hold a value.
_NO_VALUE_TYPES = {"html", "image"}
_CONTAINER_TYPES = {"panel"}

# SurveyJS's defaults for the labels a definition may leave out.
_DEFAULT_OTHER_TEXT = "Other"
_DEFAULT_NONE_TEXT = "None"
_DEFAULT_REFUSE_TEXT = "Refuse to answer"
_DEFAULT_DONT_KNOW_TEXT = "Don't know"
_DEFAULT_TRUE_TEXT = "Yes"
_DEFAULT_FALSE_TEXT = "No"


def _dicts(value):
    """The dict items of a definition list, ignoring anything malformed."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _is_scalar(value):
    return not isinstance(value, (dict, list))


def _scalars(value):
    return isinstance(value, list) and all(_is_scalar(v) for v in value)


def localized(text, fallback=""):
    """The default-locale string of a SurveyJS localizable value."""
    if isinstance(text, dict):
        for locale in ("default", "en"):
            if text.get(locale):
                return str(text[locale])
        return next((str(v) for v in text.values() if _is_scalar(v) and v), fallback)
    if text in (None, "") or isinstance(text, list):
        return fallback
    return str(text)


def _title(element):
    return localized(element.get("title"), str(element.get("name") or ""))


def _value_key(question):
    return str(question.get("valueName") or question.get("name") or "")


def iter_questions(elements):
    """Value-bearing questions in document order, with panels flattened.

    A dynamic panel is yielded as one question: its answer is the repeated group
    as a whole, not each question inside it.
    """
    for element in _dicts(elements):
        kind = element.get("type")
        if kind in _CONTAINER_TYPES:
            yield from iter_questions(element.get("elements"))
        elif kind not in _NO_VALUE_TYPES and _value_key(element):
            yield element


def definition_questions(definition):
    definition = definition if isinstance(definition, dict) else {}
    pages = definition.get("pages")
    if pages is None:
        # A single-page survey may list its elements at the top level.
        pages = [definition]
    for page in _dicts(pages):
        yield from iter_questions(page.get("elements") or page.get("questions"))


def _has_comment(question):
    return bool(
        question.get("hasComment")
        or question.get("showCommentArea")
        or question.get("hasOther")
        or question.get("showOtherItem")
    )


def _comment_key(question):
    return f"{_value_key(question)}-Comment"


# Column key for a response whose data is not an object at all, so that it still
# shows somewhere rather than as a row of blanks.
UNSTRUCTURED_KEY = object()


def build_columns(definition, extra_keys=(), *, unstructured=False):
    """The answer columns for a survey, in the order its questions appear.

    The definition is the survey as it is now, and responses may predate any edit
    to it. extra_keys are data keys seen in the responses: any the definition does
    not account for still get a column, placed last, so that answers to a question
    since removed or renamed are not silently dropped from the table or the export.
    unstructured adds a column for responses whose data is not an object.
    """
    columns = []
    titles = {}
    for question in definition_questions(definition):
        key = _value_key(question)
        # Two questions sharing a valueName store one value; show it once.
        if key in titles:
            continue
        title = _title(question)
        titles[key] = title
        columns.append(Column(key=key, title=title, question=question))
        if _has_comment(question):
            comment_key = _comment_key(question)
            titles[comment_key] = f"{title} (comment)"
            columns.append(
                Column(
                    key=comment_key,
                    title=titles[comment_key],
                    question=question,
                    is_comment=True,
                )
            )

    for key in sorted(set(extra_keys) - titles.keys()):
        is_comment = key.endswith("-Comment")
        base = key.removesuffix("-Comment")
        if is_comment and base in titles:
            title = f"{titles[base]} (comment)"
        else:
            title = f"{key} (not in current survey)"
        columns.append(Column(key=key, title=title, is_comment=is_comment))

    if unstructured:
        columns.append(Column(key=UNSTRUCTURED_KEY, title="Unrecognised response"))
    return columns


def columns_for(survey):
    """The columns for every response to survey, found without loading them."""
    text = CharField()
    responses = Response.objects.filter(survey=survey).annotate(
        data_type=Func(F("data"), function="jsonb_typeof", output_field=text)
    )
    # jsonb_object_keys raises on anything but an object, so those rows are left
    # to the unstructured column rather than failing the whole page.
    keys = (
        responses.filter(data_type="object")
        .annotate(key=Func(F("data"), function="jsonb_object_keys", output_field=text))
        .values_list("key", flat=True)
        .distinct()
    )
    unstructured = responses.exclude(data_type="object").exists()
    return build_columns(survey.definition, set(keys), unstructured=unstructured)


class _Labels:
    """Choice labels by value, tolerant of a value stored as 1 and declared as "1".

    SurveyJS keeps whatever type the value had when the response was submitted,
    and a definition edited since may spell the same choice differently.
    """

    def __init__(self, items):
        self._labels = {}
        for item in items if isinstance(items, list) else ():
            if isinstance(item, dict):
                value = item.get("value")
                if _is_scalar(value):
                    self._labels[self._key(value)] = localized(
                        item.get("text"), _raw_text(value)
                    )
            elif _is_scalar(item):
                self._labels[self._key(item)] = _raw_text(item)

    @staticmethod
    def _key(value):
        # Booleans keep their own key, since True == 1 would collide with choice 1.
        return ("bool", value) if isinstance(value, bool) else ("", str(value))

    def get(self, value, default=None):
        if not _is_scalar(value):
            return default
        return self._labels.get(self._key(value), default)


def _special_labels(question):
    return {
        "other": localized(question.get("otherText"), _DEFAULT_OTHER_TEXT),
        "none": localized(question.get("noneText"), _DEFAULT_NONE_TEXT),
        "refused": localized(question.get("refuseText"), _DEFAULT_REFUSE_TEXT),
        "dontknow": localized(question.get("dontKnowText"), _DEFAULT_DONT_KNOW_TEXT),
    }


def _choice_text(question, value, labels=None):
    """A choice's label, or the stored value itself when no choice declares it.

    A choice may have been removed since the response was submitted; its stored
    value is still the best thing to show.
    """
    labels = labels or _Labels(question.get("choices"))
    label = labels.get(value)
    if label is not None:
        return label
    special = _special_labels(question)
    if isinstance(value, str) and value in special:
        return special[value]
    return _raw_text(value)


def _raw_text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return _DEFAULT_TRUE_TEXT if value else _DEFAULT_FALSE_TEXT
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _generic(value):
    """An answer shown without a definition to interpret it."""
    if isinstance(value, dict):
        return Fields(tuple((str(k), _generic(v)) for k, v in value.items()))
    if isinstance(value, list):
        if _scalars(value):
            return ", ".join(_raw_text(v) for v in value)
        return Entries(tuple(_generic(v) for v in value))
    return _raw_text(value)


def _fields_from_questions(questions, value):
    """A group of answers keyed by question name, as in one dynamic panel."""
    if not isinstance(value, dict):
        return _generic(value)
    items = []
    seen = set()
    for question in questions:
        key = _value_key(question)
        if key in seen:
            continue
        seen.add(key)
        if key in value:
            items.append((_title(question), format_answer(question, value[key])))
        comment_key = _comment_key(question)
        if comment_key in value:
            seen.add(comment_key)
            title = f"{_title(question)} (comment)"
            items.append((title, _generic(value[comment_key])))
    items.extend((str(k), _generic(v)) for k, v in value.items() if k not in seen)
    return Fields(tuple(items))


def _matrix_column_question(matrix, column):
    """A matrix column as a question, inheriting the matrix's cell defaults."""
    return {
        **{k: v for k, v in column.items() if k not in ("cellType", "choices")},
        "type": column.get("cellType") or matrix.get("cellType") or "dropdown",
        "choices": column.get("choices") or matrix.get("choices"),
    }


def _matrix_row(matrix, row_value):
    columns = [
        _matrix_column_question(matrix, c) for c in _dicts(matrix.get("columns"))
    ]
    return _fields_from_questions(columns, row_value)


def _file_names(value):
    files = value if isinstance(value, list) else [value]
    # Uploaded content may be inlined as a data: URL; its name is what reads.
    return ", ".join(
        str(f.get("name") or "file") if isinstance(f, dict) else _raw_text(f)
        for f in files
    )


_CHOICE_TYPES = {
    "radiogroup",
    "dropdown",
    "imagepicker",
    "buttongroup",
    "checkbox",
    "tagbox",
}

# The shape each question type stores. A question whose type has changed since a
# response was submitted, say from text to a matrix, holds a value of the old shape,
# and reading it by the new type's rules would show nonsense or fail outright.
_SHAPES = {
    "text": _is_scalar,
    "comment": _is_scalar,
    "expression": _is_scalar,
    "rating": _is_scalar,
    "boolean": _is_scalar,
    "signaturepad": _is_scalar,
    "radiogroup": lambda v: _is_scalar(v) or _scalars(v),
    "dropdown": lambda v: _is_scalar(v) or _scalars(v),
    "imagepicker": lambda v: _is_scalar(v) or _scalars(v),
    "buttongroup": lambda v: _is_scalar(v) or _scalars(v),
    "checkbox": lambda v: _is_scalar(v) or _scalars(v),
    "tagbox": lambda v: _is_scalar(v) or _scalars(v),
    "ranking": lambda v: _is_scalar(v) or _scalars(v),
    "matrix": lambda v: (
        isinstance(v, dict) and all(_is_scalar(x) or _scalars(x) for x in v.values())
    ),
    "matrixdropdown": lambda v: isinstance(v, dict),
    "matrixdynamic": lambda v: isinstance(v, list),
    "paneldynamic": lambda v: isinstance(v, list),
    "multipletext": lambda v: isinstance(v, dict),
    "file": lambda v: isinstance(v, (dict, list)),
}


def format_answer(question, value):
    """One answer as a Cell, using question to turn stored values into labels.

    Never fails on a mismatch between the two: a value not of the shape the
    question's type stores is shown as it was stored, and a value no choice
    declares is shown as itself.
    """
    if value is None or value == "" or value == [] or value == {}:
        return ""
    kind = question.get("type")
    fits = _SHAPES.get(kind)
    if fits is None or not fits(value):
        return _generic(value)

    if kind in _CHOICE_TYPES:
        labels = _Labels(question.get("choices"))
        values = value if isinstance(value, list) else [value]
        return ", ".join(_choice_text(question, v, labels) for v in values)

    if kind == "ranking":
        labels = _Labels(question.get("choices"))
        values = value if isinstance(value, list) else [value]
        return Entries(tuple(_choice_text(question, v, labels) for v in values))

    if kind == "boolean":
        if value == question.get("valueTrue", True):
            return localized(question.get("labelTrue"), _DEFAULT_TRUE_TEXT)
        if value == question.get("valueFalse", False):
            return localized(question.get("labelFalse"), _DEFAULT_FALSE_TEXT)
        return _raw_text(value)

    if kind == "rating":
        return _Labels(question.get("rateValues")).get(value, _raw_text(value))

    if kind == "matrix":
        rows = _Labels(question.get("rows"))
        columns = _Labels(question.get("columns"))

        def column_text(v):
            values = v if isinstance(v, list) else [v]
            return ", ".join(columns.get(x, _raw_text(x)) for x in values)

        return Fields(
            tuple((rows.get(row, str(row)), column_text(v)) for row, v in value.items())
        )

    if kind == "matrixdropdown":
        rows = _Labels(question.get("rows"))
        return Fields(
            tuple(
                (rows.get(row, str(row)), _matrix_row(question, v))
                for row, v in value.items()
            )
        )

    if kind == "matrixdynamic":
        return Entries(tuple(_matrix_row(question, row) for row in value))

    if kind == "paneldynamic":
        questions = list(iter_questions(question.get("templateElements")))
        return Entries(tuple(_fields_from_questions(questions, p) for p in value))

    if kind == "multipletext":
        items = {i.get("name"): i for i in _dicts(question.get("items"))}
        return Fields(
            tuple(
                (_title(items[k]) if k in items else str(k), _generic(v))
                for k, v in value.items()
            )
        )

    if kind == "file":
        return _file_names(value)

    if kind == "signaturepad":
        return "(signature)"

    return _generic(value)


def format_row(columns, data):
    cells = []
    for column in columns:
        if column.key is UNSTRUCTURED_KEY:
            cells.append("" if isinstance(data, dict) else _generic(data))
            continue
        value = data.get(column.key) if isinstance(data, dict) else None
        if column.question is None or column.is_comment:
            cells.append(_generic(value))
        else:
            cells.append(format_answer(column.question, value))
    return cells


def cell_html(cell):
    """A Cell as HTML, with repeats as numbered lists and labelled parts as a table.

    Repeats that all share one set of labels, as a dynamic panel's do, render as
    one small table with a row per repeat instead, which is far easier to scan.
    """
    if isinstance(cell, str):
        return format_html("{}", cell)

    if isinstance(cell, Fields):
        return format_html(
            '<table class="reef-answer-fields"><tbody>{}</tbody></table>',
            format_html_join(
                "",
                '<tr><th scope="row">{}</th><td>{}</td></tr>',
                ((label, cell_html(v)) for label, v in cell.items),
            ),
        )

    if all(isinstance(item, Fields) for item in cell.items) and cell.items:
        labels = []
        for item in cell.items:
            for label, _ in item.items:
                if label not in labels:
                    labels.append(label)
        head = format_html_join("", '<th scope="col">{}</th>', ((lb,) for lb in labels))
        body = format_html_join(
            "",
            '<tr><th scope="row">{}</th>{}</tr>',
            (
                (
                    index,
                    format_html_join(
                        "",
                        "<td>{}</td>",
                        (
                            (cell_html(dict(item.items).get(label, "")),)
                            for label in labels
                        ),
                    ),
                )
                for index, item in enumerate(cell.items, start=1)
            ),
        )
        return format_html(
            '<table class="reef-answer-entries"><thead><tr><th scope="col">#</th>{}'
            "</tr></thead><tbody>{}</tbody></table>",
            head,
            body,
        )

    return format_html(
        '<ol class="reef-answer-list">{}</ol>',
        format_html_join("", "<li>{}</li>", ((cell_html(v),) for v in cell.items)),
    )


def cell_text(cell, indent=""):
    """A Cell as plain text for one spreadsheet cell, structure kept by line breaks."""
    if isinstance(cell, str):
        return cell
    lines = []
    if isinstance(cell, Fields):
        for label, value in cell.items:
            text = cell_text(value, f"{indent}  ")
            if "\n" in text:
                lines.append(f"{indent}{label}:\n{text}")
            else:
                lines.append(f"{indent}{label}: {text.strip()}")
    else:
        for index, value in enumerate(cell.items, start=1):
            text = cell_text(value, f"{indent}  ")
            if "\n" in text:
                lines.append(f"{indent}#{index}\n{text}")
            else:
                lines.append(f"{indent}#{index}: {text.strip()}")
    return "\n".join(lines)


# A spreadsheet runs a cell starting with one of these as a formula. Answers come
# from the public, so the export must not hand anyone a way to put one there.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def csv_safe(text):
    if text.startswith(_FORMULA_PREFIXES) and not _NUMBER.fullmatch(text):
        return f"'{text}"
    return text


class _Echo:
    """A file-like object that hands back what is written, for streaming csv."""

    def write(self, value):
        return value


def respondent_label(response):
    user = response.submitted_by
    return str(user) if user else "Anonymous"


FIXED_HEADERS = ("Submitted", "Respondent")


def iter_csv(survey, columns):
    """Every response to survey as CSV lines, streamed rather than held in memory."""
    writer = csv.writer(_Echo())
    # A byte order mark, so that Excel reads the file as UTF-8 rather than guessing.
    yield "﻿"
    yield writer.writerow([*FIXED_HEADERS, *(csv_safe(c.title) for c in columns)])
    responses = (
        Response.objects.filter(survey=survey)
        .select_related("submitted_by")
        .order_by("-submitted_at", "-pk")
    )
    for response in responses.iterator(chunk_size=500):
        cells = format_row(columns, response.data)
        yield writer.writerow(
            [
                response.submitted_at.isoformat(timespec="seconds"),
                csv_safe(respondent_label(response)),
                *(csv_safe(cell_text(cell)) for cell in cells),
            ]
        )
