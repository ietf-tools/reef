# Copyright The IETF Trust 2026, All Rights Reserved
import io


class ProgressOutput(io.StringIO):
    """A command's stdout, kept whole for the finished page and mirrored line by
    line onto a run row's progress_message for the polling one.

    A stream rather than a callback threaded through the command: the command
    already writes a line at each step, and BaseCommand hands every one of them
    to whatever stdout it was given, one write per line.
    """

    def __init__(self, model, run_id):
        super().__init__()
        self.model = model
        self.run_id = run_id

    def write(self, text):
        written = super().write(text)
        line = text.strip()
        if line:
            self.model.objects.filter(pk=self.run_id).update(
                progress_message=line.splitlines()[-1][:255]
            )
        return written
