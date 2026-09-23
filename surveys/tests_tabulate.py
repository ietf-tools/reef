# Copyright The IETF Trust 2026, All Rights Reserved
import csv
import io

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from . import tabulate
from .models import Response
from .tabulate import Entries, Fields
from .tests import make_survey

User = get_user_model()

DEFINITION = {
    "pages": [
        {
            "name": "p1",
            "elements": [
                {"type": "html", "name": "intro", "html": "<p>Hi</p>"},
                {"type": "rating", "name": "q1", "title": "How useful?"},
                {
                    "type": "radiogroup",
                    "name": "colour",
                    "title": {"default": "Favourite colour", "fr": "Couleur"},
                    "choices": [
                        {"value": "r", "text": "Red"},
                        {"value": "g", "text": {"default": "Green"}},
                    ],
                    "showOtherItem": True,
                },
                {
                    "type": "panel",
                    "name": "panel1",
                    "elements": [
                        {
                            "type": "checkbox",
                            "name": "tools",
                            "title": "Tools used",
                            "choices": ["git", {"value": "svn", "text": "Subversion"}],
                        },
                        {"type": "boolean", "name": "ok", "title": "Happy?"},
                    ],
                },
            ],
        },
        {
            "name": "p2",
            "elements": [
                {
                    "type": "paneldynamic",
                    "name": "authors",
                    "title": "Authors",
                    "templateElements": [
                        {"type": "text", "name": "name", "title": "Name"},
                        {
                            "type": "dropdown",
                            "name": "role",
                            "title": "Role",
                            "choices": [{"value": "ed", "text": "Editor"}],
                        },
                    ],
                },
                {
                    "type": "matrix",
                    "name": "grid",
                    "title": "Grid",
                    "rows": [{"value": "speed", "text": "Speed"}],
                    "columns": [
                        {"value": 1, "text": "Poor"},
                        {"value": 5, "text": "Great"},
                    ],
                },
                {
                    "type": "matrixdynamic",
                    "name": "deps",
                    "title": "Dependencies",
                    "columns": [
                        {"name": "lib", "title": "Library", "cellType": "text"},
                        {"name": "used", "cellType": "boolean"},
                    ],
                },
            ],
        },
    ]
}


class ColumnTests(SimpleTestCase):
    def test_columns_follow_definition_order_and_skip_layout(self):
        columns = tabulate.build_columns(DEFINITION)
        self.assertEqual(
            [c.title for c in columns],
            [
                "How useful?",
                "Favourite colour",
                "Favourite colour (comment)",
                "Tools used",
                "Happy?",
                "Authors",
                "Grid",
                "Dependencies",
            ],
        )

    def test_keys_no_longer_in_definition_still_get_columns(self):
        """Answers to a question since removed from the survey must not vanish."""
        columns = tabulate.build_columns(DEFINITION, {"q1", "removed", "q1-Comment"})
        self.assertEqual(
            [(c.key, c.title) for c in columns[-2:]],
            [
                ("q1-Comment", "How useful? (comment)"),
                ("removed", "removed (not in current survey)"),
            ],
        )


class FormatTests(SimpleTestCase):
    def test_flat_answers_use_labels(self):
        columns = tabulate.build_columns(DEFINITION)
        cells = tabulate.format_row(
            columns,
            {
                "q1": 4,
                "colour": "other",
                "colour-Comment": "Mauve",
                "tools": ["git", "svn"],
                "ok": False,
            },
        )
        self.assertEqual(cells[:5], ["4", "Other", "Mauve", "git, Subversion", "No"])
        self.assertEqual(cells[5:], ["", "", ""])

    def test_repeating_and_matrix_answers_are_structured(self):
        columns = tabulate.build_columns(DEFINITION)
        cells = tabulate.format_row(
            columns,
            {
                "authors": [{"name": "Ana", "role": "ed"}, {"name": "Bo"}],
                "grid": {"speed": 5},
                "deps": [{"lib": "django", "used": True}],
            },
        )
        self.assertEqual(
            cells[5],
            Entries(
                (
                    Fields((("Name", "Ana"), ("Role", "Editor"))),
                    Fields((("Name", "Bo"),)),
                )
            ),
        )
        self.assertEqual(cells[6], Fields((("Speed", "Great"),)))
        self.assertEqual(
            cells[7], Entries((Fields((("Library", "django"), ("used", "Yes"))),))
        )

    def test_answers_survive_the_definition_changing_under_them(self):
        """Responses predate edits to the survey: a question whose type changed,
        a choice that was removed, or a value stored as another JSON type must
        still read sensibly rather than fail or vanish."""
        edited = {
            "elements": [
                # Was a text question; now a matrix.
                {"type": "matrix", "name": "was_text", "rows": ["a"], "columns": [1]},
                # Was a paneldynamic; now a dropdown.
                {"type": "dropdown", "name": "was_panel", "choices": ["x"]},
                {"type": "radiogroup", "name": "removed_choice", "choices": ["kept"]},
                {
                    "type": "radiogroup",
                    "name": "typed",
                    "choices": [{"value": 1, "text": "One"}],
                },
                {
                    "type": "rating",
                    "name": "rate",
                    "rateValues": [{"value": "5", "text": "Top"}],
                },
                {
                    "type": "checkbox",
                    "name": "junk_choices",
                    "choices": [None, 3, {"text": "x"}],
                },
                {
                    "type": "paneldynamic",
                    "name": "panel",
                    "templateElements": "garbage",
                },
                "not an element",
            ]
        }
        cells = tabulate.format_row(
            tabulate.build_columns(edited),
            {
                "was_text": "free text",
                "was_panel": [{"name": "Ana"}],
                "removed_choice": "dropped",
                "typed": "1",
                "rate": 5,
                "junk_choices": [3, "other"],
                "panel": [{"name": "Bo"}, "odd"],
            },
        )
        self.assertEqual(
            cells,
            [
                "free text",
                Entries((Fields((("name", "Ana"),)),)),
                "dropped",
                "One",
                "Top",
                "3, Other",
                Entries((Fields((("name", "Bo"),)), "odd")),
            ],
        )

    def test_malformed_definition_and_data_do_not_fail(self):
        definitions = (
            None,
            [],
            "x",
            {"pages": "x"},
            {"pages": [None, {"elements": 3}]},
        )
        for definition in definitions:
            with self.subTest(definition=definition):
                columns = tabulate.build_columns(definition, {"q"})
                cells = tabulate.format_row(columns, {"q": {"a": [1]}})
                self.assertEqual(cells, [Fields((("a", "1"),))])

    def test_unstructured_data_gets_its_own_column(self):
        columns = tabulate.build_columns(DEFINITION, unstructured=True)
        self.assertEqual(columns[-1].title, "Unrecognised response")
        self.assertEqual(tabulate.format_row(columns, [1, 2])[-1], "1, 2")
        self.assertEqual(tabulate.format_row(columns, {})[-1], "")

    def test_html_escapes_answers_and_tables_repeats(self):
        cell = Entries((Fields((("Name", "<b>Ana</b>"),)), Fields((("Role", "Ed"),))))
        html = str(tabulate.cell_html(cell))
        self.assertIn("&lt;b&gt;Ana&lt;/b&gt;", html)
        self.assertIn('<table class="reef-answer-entries">', html)
        self.assertIn('<th scope="col">Name</th><th scope="col">Role</th>', html)

    def test_text_flattens_structure_into_lines(self):
        cell = Entries((Fields((("Name", "Ana"), ("Role", "Editor"))),))
        self.assertEqual(tabulate.cell_text(cell), "#1\n  Name: Ana\n  Role: Editor")

    def test_csv_neutralises_formulas_but_not_negative_numbers(self):
        self.assertEqual(tabulate.csv_safe("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(tabulate.csv_safe("-3"), "-3")
        self.assertEqual(tabulate.csv_safe("plain"), "plain")


class ResponsesViewTests(TestCase):
    def setUp(self):
        self.survey = make_survey(slug="t1", definition=DEFINITION)
        self.staff = User.objects.create(
            username="admin", oidc_sub="s-tab", is_staff=True
        )

    def test_pages_require_staff(self):
        for url in (
            "/admin/survey-builder/results/",
            f"/admin/survey-builder/results/{self.survey.pk}/",
            f"/admin/survey-builder/results/{self.survey.pk}/export.csv",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)

    def test_list_includes_withdrawn_surveys(self):
        withdrawn = make_survey(slug="gone", title="Gone")
        withdrawn.soft_delete()
        self.client.force_login(self.staff)
        resp = self.client.get("/admin/survey-builder/results/")
        self.assertContains(resp, f"/admin/survey-builder/results/{withdrawn.pk}/")
        self.assertContains(resp, "withdrawn")

    def test_table_shows_labels_and_paginates(self):
        Response.objects.bulk_create(
            Response(survey=self.survey, data={"colour": "g"}) for _ in range(51)
        )
        self.client.force_login(self.staff)
        url = f"/admin/survey-builder/results/{self.survey.pk}/"
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "Favourite colour")
        self.assertContains(first, "<td>Green</td>", count=50)
        self.assertContains(first, "?page=2")
        second = self.client.get(f"{url}?page=2")
        self.assertContains(second, "<td>Green</td>", count=1)

    def test_csv_exports_every_response(self):
        Response.objects.bulk_create(
            Response(survey=self.survey, data={"q1": 3, "tools": ["svn"]})
            for _ in range(60)
        )
        Response.objects.create(survey=self.survey, data={"legacy": "kept"})
        # Not an object, which jsonb_object_keys would refuse outright.
        Response.objects.create(survey=self.survey, data=["stray"])
        self.client.force_login(self.staff)
        resp = self.client.get(
            f"/admin/survey-builder/results/{self.survey.pk}/export.csv"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("t1-responses.csv", resp["Content-Disposition"])
        body = b"".join(resp.streaming_content).decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(body)))
        header, data = rows[0], rows[1:]
        self.assertEqual(header[:3], ["Submitted", "Respondent", "How useful?"])
        self.assertEqual(
            header[-2:],
            ["legacy (not in current survey)", "Unrecognised response"],
        )
        self.assertEqual(len(data), 62)
        tools = header.index("Tools used")
        self.assertEqual(sum(row[tools] == "Subversion" for row in data), 60)
        self.assertTrue(any(row[-2] == "kept" for row in data))
        self.assertTrue(any(row[-1] == "stray" for row in data))
