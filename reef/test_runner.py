# Copyright The IETF Trust 2026, All Rights Reserved
"""The project's test runner, which is here to stop tests reaching the network.

reef.rfcmeta fetches Red's published index, and anything that resolves a document
identifier eventually reaches it. Call sites reach it by accident, and the only
symptom is a suite that gets mysteriously slower, found by looking at timings rather
than by anything failing.

So the default is that a test cannot do it. Anything wanting the index stubs it with
reef.testing.stub_rfc_index, and anything genuinely exercising the fetch patches
urlopen itself, which overrides this.
"""

from unittest import mock

from django.test.runner import DiscoverRunner


def _refuse(*args, **kwargs):
    raise AssertionError(
        "A test tried to open a network connection. Use "
        "reef.testing.stub_rfc_index, or patch urllib.request.urlopen in the test "
        "that means to exercise fetching."
    )


class ReefTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._no_network = mock.patch("urllib.request.urlopen", side_effect=_refuse)
        self._no_network.start()

    def teardown_test_environment(self, **kwargs):
        self._no_network.stop()
        super().teardown_test_environment(**kwargs)
