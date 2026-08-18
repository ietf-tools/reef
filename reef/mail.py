# Copyright The IETF Trust 2026, All Rights Reserved
"""Outgoing mail, with Reef's defaults applied in one place.

Thin wrappers over django.core.mail, so that every sender agrees on the from
address, the Message-ID domain, and the headers that mark a message as
automatically generated, rather than each caller remembering them.

Follows Purple's purple/mail.py, with two deliberate differences.

fail_silently defaults to False. Purple's mail is composed by a staff user who
sees the failure in the UI, so swallowing the exception is survivable there.
Reef's is unattended notification: a swallowed exception means the retry never
fires and the message is lost with nothing to show it.

Purple's EmailMessage subclass documents its customization as defaulting the
from address, which Django has done by itself since long before either project
(EmailMessage.__init__ sets from_email or DEFAULT_FROM_EMAIL). What is worth
subclassing for is the Message-ID: Django generates one from the local
hostname, which under Kubernetes is a pod name that is both meaningless to a
postmaster reading a bounce and more than Reef needs to disclose.
"""

from email.utils import make_msgid

from django.conf import settings
from django.core.mail import EmailMessage as _EmailMessage
from django.core.mail import send_mail as _send_mail


def make_message_id():
    """A Message-ID in the deployment's own domain."""
    return make_msgid(domain=getattr(settings, "MESSAGE_ID_DOMAIN", None) or None)


def send_mail(to, subject, msg, frm=None, fail_silently=False):
    """send_mail for a one-off message.

    Customizations over django.core.mail.send_mail:
      * a single recipient may be given as a bare string
      * the from address defaults to settings.DEFAULT_FROM_EMAIL

    For notification mail use EmailMessage, which carries the headers that say
    a human did not send it.
    """
    if not frm:
        frm = settings.DEFAULT_FROM_EMAIL
    if isinstance(to, str):
        to = [to]
    _send_mail(subject, msg, frm, to, fail_silently=fail_silently)


class EmailMessage(_EmailMessage):
    """EmailMessage with Reef's defaults.

    Customizations:
      * Message-ID is generated in MESSAGE_ID_DOMAIN rather than from the
        local hostname
      * Auto-Submitted: auto-generated (RFC 3834), so that vacation
        autoresponders and ticket systems do not reply to a notification
      * List-Unsubscribe pointing at the subscription management page, when
        REEF_SUBSCRIPTIONS_URL names one

    A caller that passes any of these headers itself keeps its own value.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        given = {name.lower() for name in self.extra_headers}
        if "message-id" not in given:
            self.extra_headers["Message-ID"] = make_message_id()
        if "auto-submitted" not in given:
            self.extra_headers["Auto-Submitted"] = "auto-generated"
        # No List-Unsubscribe-Post: one-click unsubscribe (RFC 8058) needs an
        # unauthenticated tokenized endpoint to POST to, and Reef's unsubscribe
        # is an authenticated delete. Offering the header without that endpoint
        # would advertise a capability that does not answer.
        unsubscribe = getattr(settings, "REEF_SUBSCRIPTIONS_URL", "")
        if unsubscribe and "list-unsubscribe" not in given:
            self.extra_headers["List-Unsubscribe"] = f"<{unsubscribe}>"
